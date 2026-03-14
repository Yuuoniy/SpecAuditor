#!/usr/bin/env python3
"""
Bug Detection Audit Script

Evaluates bug detection results by querying an LLM with patch context,
specifications, and code snippets. The model may request up to a limited number
of additional context rounds per finding.
"""

import argparse
import json
import os
import subprocess
import sys
import warnings
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

try:
    from scripts.utils.artifact_utils import configure_script_imports
except ImportError:
    from utils.artifact_utils import configure_script_imports

configure_script_imports(__file__)

from prompt_loader import PromptLoader  # type: ignore
from openai_client import OpenAIClient  # type: ignore
from get_patch_full_diff import PatchFullDiffExtractor  # type: ignore

warnings.filterwarnings(
    "ignore",
    category=FutureWarning,
    message=r"Language\(path, name\) is deprecated\. Use Language\(ptr, name\) instead\.",
)

try:
    from CodeSearcher import CodeSearcher  # type: ignore
except ImportError:
    CodeSearcher = None


class BugDetectionAuditor:

    def __init__(
        self,
        repo_path: str,
        source_dir: Optional[str] = None,
        max_more_context: int = 3,
        max_workers: int = 4,
        llm_timeout: Optional[float] = 60,
        llm_max_retries: int = 3,
        llm_model: str = "gpt-4o-mini",
    ):
        self.repo_path = repo_path
        self.source_dir = source_dir or repo_path
        self.max_more_context = max_more_context
        self.max_workers = max_workers

        self.llm_model = llm_model
        
        self.prompt_loader = PromptLoader()
        self.llm_client = OpenAIClient()
        # Set the model for the LLM client
        self.llm_client.set_model(llm_model)
        
        self.patch_extractor = PatchFullDiffExtractor(self.repo_path)

        self.code_searcher = None
        if CodeSearcher:
            try:
                import tree_sitter  # noqa: F401  # Ensure dependency exists

                self.code_searcher = CodeSearcher(self.source_dir)
                print("✅ CodeSearcher initialized for source context.")
            except Exception as exc:  # Broad catch to avoid breaking workflow
                print(f"⚠️  CodeSearcher not available: {exc}")
        else:
            print("⚠️  CodeSearcher module not importable; code context will be limited.")
        
        # Token usage statistics with thread safety
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.total_api_calls = 0
        self._token_stats_lock = threading.Lock()

    @staticmethod
    def _escape_braces(text: str) -> str:
        return text.replace("{", "{{").replace("}", "}}")

    @staticmethod
    def _clip_text(text: str, limit: Optional[int], label: str) -> str:
        if limit is not None and len(text) > limit:
            clipped = text[:limit]
            omitted = len(text) - limit
            return f"{clipped}\n\n[Truncated {omitted} characters from {label}]"
        return text

    @staticmethod
    def _to_clean_str(value: Any, fallback: str = "") -> str:
        if value is None:
            return fallback
        if isinstance(value, float) and pd.isna(value):
            return fallback
        return str(value)

    @staticmethod
    def _extract_json_block(content: str) -> Optional[str]:
        start = content.find("{")
        while start != -1:
            depth = 0
            for idx in range(start, len(content)):
                ch = content[idx]
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        candidate = content[start : idx + 1]
                        try:
                            json.loads(candidate)
                            return candidate
                        except json.JSONDecodeError:
                            break
            start = content.find("{", start + 1)
        return None

    def _parse_json_response(self, response: str) -> Dict[str, Any]:
        try:
            content = response.strip()
            if "```json" in content:
                start = content.find("```json") + 7
                end = content.find("```", start)
                content = content[start:end].strip()
            elif content.startswith("```") and content.endswith("```"):
                content = content[3:-3].strip()
            return json.loads(content)
        except Exception as exc:
            candidate = self._extract_json_block(content if "content" in locals() else response)
            if candidate:
                try:
                    return json.loads(candidate)
                except json.JSONDecodeError:
                    pass
            print(f"  ⚠️  Failed to parse JSON response: {exc}")
            preview = response[:200].replace("\n", " ")
            print(f"  Raw response preview: {preview}...")
            return {}

    def _get_patch_context(self, hexsha: str) -> Dict[str, str]:
        diff = self.patch_extractor.get_full_function_diff(hexsha) or "Patch diff unavailable."
        commit_message = ""
        try:
            result = subprocess.run(
                ["git", "show", hexsha, "--format=%B", "--no-patch"],
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                check=False,
            )
            commit_message = result.stdout.strip()
        except Exception as exc:
            commit_message = f"Error retrieving commit message: {exc}"

        if not commit_message:
            commit_message = "Commit message unavailable."

        return {"patch_code": diff, "patch_description": commit_message}

    def _get_function_source_code(self, function_name: str) -> str:
        if not function_name:
            return "No function name provided."
        if self.code_searcher:
            try:
                results = self.code_searcher.query_given_func_code(function_name)
                if results:
                    return results[0] if isinstance(results, list) else str(results)
                return f"No source code found for function: {function_name}"
            except Exception as exc:
                return f"Error retrieving source code for {function_name}: {exc}"
        return "CodeSearcher unavailable; cannot retrieve source code."

    def _get_function_usage_examples(self, function_name: str) -> str:
        if not function_name:
            return "No function name provided."
        if self.code_searcher:
            try:
                results = self.code_searcher.query_given_func_usage(function_name)
                if results:
                    if isinstance(results, list):
                        entries = results[:3]
                        return "\n\n".join(
                            f"Usage Example {idx + 1}:\n{entry}"
                            for idx, entry in enumerate(entries)
                        )
                    return f"Usage Example:\n{results}"
                return f"No usage examples found for function: {function_name}"
            except Exception as exc:
                return f"Error retrieving usage examples for {function_name}: {exc}"
        return "CodeSearcher unavailable; cannot retrieve usage examples."

    def _format_previous_interactions(self, history: List[Dict[str, Any]]) -> str:
        if not history:
            return "None. This is the first request."

        chunks = []
        for idx, item in enumerate(history, 1):
            requests = item.get("requests", [])
            provided = item.get("provided", [])

            req_descriptions = []
            for req in requests:
                req_type = req.get("request_type", "unknown")
                func_name = req.get("func_name", "unknown")
                req_descriptions.append(f"{req_type} for {func_name}")
            req_text = ", ".join(req_descriptions) if req_descriptions else "No request details."

            provided_snippets = []
            for prov in provided:
                req = prov.get("request", {})
                req_type = req.get("request_type", "unknown")
                func_name = req.get("func_name", "unknown")
                content = prov.get("content", "")
                provided_snippets.append(
                    f"[{req_type} :: {func_name}]\n{content}"
                )
            provided_text = "\n\n".join(provided_snippets) if provided_snippets else "No context was available."

            chunks.append(f"Round {idx}: requested {req_text}.\nProvided context:\n{provided_text}")

        return "\n\n".join(chunks)

    def _resolve_context_request(self, request: Dict[str, Any]) -> Dict[str, Any]:
        req_type = request.get("request_type", "unknown")
        func_name = request.get("func_name", "")

        if req_type == "source_code":
            content = self._get_function_source_code(func_name)
        elif req_type == "usage_code":
            content = self._get_function_usage_examples(func_name)
        else:
            content = f"Unsupported request type '{req_type}'."

        return {"request": request, "content": content}

    def _build_user_prompt(
        self,
        base_context: Dict[str, str],
        row: pd.Series,
        history: List[Dict[str, Any]],
    ) -> str:
        remaining = max(self.max_more_context - len(history), 0)
        user_template = self.prompt_loader.get_bug_audit_prompt("user")

        formatted = user_template.format(
            hexsha=self._escape_braces(base_context["hexsha"]),
            violation_function=self._escape_braces(base_context["violation_function"]),
            target_function=self._escape_braces(base_context["target_function"]),
            spec_target=self._escape_braces(base_context["spec_target"]),
            spec_predicate=self._escape_braces(base_context["spec_predicate"]),
            weggli_query=self._escape_braces(base_context["weggli_query"]),
            total_matches=self._escape_braces(base_context["total_matches"]),
            analysis_summary=self._escape_braces(base_context["analysis_summary"]),
            confidence=self._escape_braces(base_context["confidence"]),
            patch_description=self._escape_braces(base_context["patch_description"]),
            patch_code=self._escape_braces(base_context["patch_code"]),
            violation_source=self._escape_braces(base_context["violation_source"]),
            violation_usage=self._escape_braces(base_context["violation_usage"]),
            previous_interactions=self._escape_braces(self._format_previous_interactions(history)),
            remaining_requests=str(remaining),
            max_more_context=str(self.max_more_context),
        )
        return formatted

    def audit_row(self, row: pd.Series) -> Dict[str, Any]:
        hexsha = self._to_clean_str(row.get("hexsha"))
        violation_func = self._to_clean_str(row.get("violation_function_name"))
        target_function = self._to_clean_str(row.get("target_function"))

        patch_ctx = self._get_patch_context(hexsha)
        base_context = {
            "hexsha": hexsha,
            "violation_function": violation_func or "(unknown)",
            "target_function": target_function or "(unknown)",
            "spec_target": self._clip_text(
                self._to_clean_str(row.get("spec_target"), "(not provided)"), 2000, "spec_target"
            ),
            "spec_predicate": self._clip_text(
                self._to_clean_str(row.get("spec_predicate"), "(not provided)"), 2000, "spec_predicate"
            ),
            "weggli_query": self._clip_text(
                self._to_clean_str(row.get("weggli_query"), "(not provided)"), 2000, "weggli_query"
            ),
            "total_matches": self._to_clean_str(row.get("total_matches"), "0"),
            "analysis_summary": self._clip_text(
                self._to_clean_str(row.get("analysis"), "No prior analysis."), 4000, "analysis"
            ),
            "confidence": self._to_clean_str(row.get("confidence"), "UNKNOWN"),
            "patch_description": self._clip_text(patch_ctx["patch_description"], 4000, "patch_description"),
            "patch_code": self._clip_text(patch_ctx["patch_code"], 8000, "patch_code"),
            "violation_source": self._clip_text(
                self._get_function_source_code(violation_func), 6000, "violation_source"
            ),
            "violation_usage": self._clip_text(
                self._get_function_usage_examples(violation_func), 4000, "violation_usage"
            ),
        }

        history: List[Dict[str, Any]] = []
        raw_responses: List[str] = []
        final_response: Dict[str, Any] = {}
        
        # Track token usage for this row
        row_input_tokens = 0
        row_output_tokens = 0

        system_prompt = self.prompt_loader.get_bug_audit_prompt(
            "system", max_more_context=self.max_more_context
        )

        while True:
            user_prompt = self._build_user_prompt(base_context, row, history)
            response_text, input_tokens, output_tokens = self.llm_client.send_message_with_tokens(user_prompt, system_prompt)
            
            row_input_tokens += input_tokens
            row_output_tokens += output_tokens
            
            with self._token_stats_lock:
                self.total_input_tokens += input_tokens
                self.total_output_tokens += output_tokens
                self.total_api_calls += 1
            
            raw_responses.append(response_text)
            print(f"LLM raw response preview: {response_text[:200] if response_text else 'None'}{'...' if response_text and len(response_text) > 200 else ''}")

            if not response_text or response_text.strip().lower() == "unknown":
                final_response = {
                    "type": "final_decision",
                    "decision": "uncertain",
                    "explanation": "Model call failed or returned no content.",
                }
                break

            parsed = self._parse_json_response(response_text)
            if not parsed:
                final_response = {
                    "type": "final_decision",
                    "decision": "uncertain",
                    "explanation": "Could not parse model response as JSON.",
                }
                break

            if parsed.get("type") == "more_context":
                if len(history) >= self.max_more_context:
                    final_response = {
                        "type": "final_decision",
                        "decision": "uncertain",
                        "explanation": "Model requested additional context beyond the allowed limit.",
                    }
                    break

                requests = parsed.get("requests", [])
                if not requests:
                    final_response = {
                        "type": "final_decision",
                        "decision": "uncertain",
                        "explanation": "Model asked for more context but did not specify requests.",
                    }
                    break

                provided = [self._resolve_context_request(req) for req in requests]
                history.append({"requests": requests, "provided": provided})
                continue

            if parsed.get("type") == "final_decision":
                final_response = parsed
                break

            final_response = {
                "type": "final_decision",
                "decision": "uncertain",
                "explanation": "Unexpected response format from model.",
            }
            break

        decision = final_response.get("decision", "uncertain")
        explanation = final_response.get("explanation", "")

        return {
            "hexsha": hexsha,
            "violation_function": violation_func,
            "decision": decision,
            "explanation": explanation,
            "context_rounds": len(history),
            "requests_history": history,
            "raw_model_responses": raw_responses,
            "final_response_json": final_response,
            "spec_target": base_context["spec_target"],
            "spec_predicate": base_context["spec_predicate"],
            "has_violation": self._to_clean_str(row.get("has_violation")),
            "total_matches": base_context["total_matches"],
            "confidence": base_context["confidence"],
            "input_tokens": row_input_tokens,
            "output_tokens": row_output_tokens,
            "total_tokens": row_input_tokens + row_output_tokens,
        }

    def process(
        self,
        input_csv: str,
        output_json: str,
        output_csv: Optional[str] = None,
        limit: Optional[int] = None,
        only_violations: bool = False,
    ) -> None:
        df = pd.read_csv(input_csv)
        if only_violations and "has_violation" in df.columns:
            df = df[df["has_violation"] == True]  # noqa: E712

        if limit is not None:
            df = df.head(limit)

        records = df.to_dict("records")
        indexed_records = list(enumerate(records))
        total = len(indexed_records)

        if total == 0:
            with open(output_json, "w", encoding="utf-8") as fout:
                json.dump([], fout, ensure_ascii=False, indent=2)
            if output_csv:
                pd.DataFrame([]).to_csv(output_csv, index=False)
            print("\n✅ No records to process.")
            return

        worker_count = max(1, min(self.max_workers, total))
        ordered_results: List[Optional[Dict[str, Any]]] = [None] * total
        ordered_rows: List[Optional[Dict[str, Any]]] = [None] * total
        save_lock = threading.Lock()

        def save_partial_results():
            with save_lock:
                completed_results = [res for res in ordered_results if res is not None]
                completed_rows = [row for row in ordered_rows if row is not None]
                if output_json:
                    with open(output_json, "w", encoding="utf-8") as fout:
                        json.dump(completed_results, fout, ensure_ascii=False, indent=2)
                if output_csv and completed_rows:
                    pd.DataFrame(completed_rows).to_csv(output_csv, index=False)

        def _worker(order_idx: int, record: Dict[str, Any]):
            row_series = pd.Series(record)
            print(f"\n[{order_idx + 1}/{total}] Auditing {record.get('hexsha', '')} :: {record.get('violation_function_name', '')}")
            result = self.audit_row(row_series)
            input_row = {
                key: (None if pd.isna(value) else value)
                for key, value in row_series.items()
            }
            combined_row = {**input_row, **result}
            return order_idx, result, combined_row

        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = [
                executor.submit(_worker, idx, record)
                for idx, record in indexed_records
            ]
            for future in as_completed(futures):
                order_idx, result, combined_row = future.result()
                ordered_results[order_idx] = result
                ordered_rows[order_idx] = combined_row
                save_partial_results()

        final_results = [res for res in ordered_results if res is not None]
        final_rows = [row for row in ordered_rows if row is not None]

        # Add token statistics to the results
        token_stats = {
            "model_info": {
                "model_name": self.llm_model,
                "timestamp": datetime.now().isoformat()
            },
            "processing_summary": {
                "total_records_processed": len(final_results),
                "total_api_calls": self.total_api_calls,
                "average_api_calls_per_record": self.total_api_calls / len(final_results) if final_results else 0
            },
            "token_usage": {
                "total_input_tokens": self.total_input_tokens,
                "total_output_tokens": self.total_output_tokens,
                "total_tokens": self.total_input_tokens + self.total_output_tokens
            },
            "averages_per_api_call": {
                "input_tokens": self.total_input_tokens / self.total_api_calls if self.total_api_calls > 0 else 0,
                "output_tokens": self.total_output_tokens / self.total_api_calls if self.total_api_calls > 0 else 0,
                "total_tokens": (self.total_input_tokens + self.total_output_tokens) / self.total_api_calls if self.total_api_calls > 0 else 0
            },
            "averages_per_record": {
                "input_tokens": self.total_input_tokens / len(final_results) if final_results else 0,
                "output_tokens": self.total_output_tokens / len(final_results) if final_results else 0,
                "total_tokens": (self.total_input_tokens + self.total_output_tokens) / len(final_results) if final_results else 0
            }
        }
        
        final_output = {
            "token_statistics": token_stats,
            "audit_results": final_results
        }
        
        with open(output_json, "w", encoding="utf-8") as fout:
            json.dump(final_output, fout, ensure_ascii=False, indent=2)
        print(f"\n✅ Saved JSON results with token statistics to {output_json}")

        if output_csv:
            pd.DataFrame(final_rows).to_csv(output_csv, index=False)
            print(f"✅ Saved CSV summary to {output_csv}")
            
            # Also save token statistics to a separate summary file
            model_name = self.llm_model.replace("/", "_").replace("-", "_")
            stats_csv = output_csv.replace('.csv', f'_{model_name}_token_stats.csv')
            stats_df = pd.DataFrame([{
                'total_records': len(final_results),
                'total_api_calls': self.total_api_calls,
                'total_input_tokens': self.total_input_tokens,
                'total_output_tokens': self.total_output_tokens,
                'total_tokens': self.total_input_tokens + self.total_output_tokens,
                'avg_input_per_call': self.total_input_tokens / self.total_api_calls if self.total_api_calls > 0 else 0,
                'avg_output_per_call': self.total_output_tokens / self.total_api_calls if self.total_api_calls > 0 else 0,
                'avg_tokens_per_call': (self.total_input_tokens + self.total_output_tokens) / self.total_api_calls if self.total_api_calls > 0 else 0,
                'avg_input_per_record': self.total_input_tokens / len(final_results) if final_results else 0,
                'avg_output_per_record': self.total_output_tokens / len(final_results) if final_results else 0,
                'avg_tokens_per_record': (self.total_input_tokens + self.total_output_tokens) / len(final_results) if final_results else 0
            }])
            stats_df.to_csv(stats_csv, index=False)
            print(f"✅ Saved token statistics summary to {stats_csv}")
        
        print("\n" + "=" * 70)
        print("📊 Bug Audit Token Usage Statistics")
        print("=" * 70)
        print(f"🔢 Processing Summary:")
        print(f"   • Total records processed: {len(final_results)}")
        print(f"   • Total API calls made: {self.total_api_calls}")
        print(f"   • Average API calls per record: {self.total_api_calls / len(final_results):.1f}" if final_results else "   • No records processed")
        
        print(f"\n💰 Token Usage:")
        print(f"   • Total input tokens: {self.total_input_tokens:,}")
        print(f"   • Total output tokens: {self.total_output_tokens:,}")
        print(f"   • Total tokens: {self.total_input_tokens + self.total_output_tokens:,}")
        
        if self.total_api_calls > 0:
            print(f"\n📈 Average per API call:")
            print(f"   • Input tokens: {self.total_input_tokens / self.total_api_calls:.1f}")
            print(f"   • Output tokens: {self.total_output_tokens / self.total_api_calls:.1f}")
            print(f"   • Total tokens: {(self.total_input_tokens + self.total_output_tokens) / self.total_api_calls:.1f}")
        
        if final_results:
            print(f"\n📊 Average per record:")
            print(f"   • Input tokens: {self.total_input_tokens / len(final_results):.1f}")
            print(f"   • Output tokens: {self.total_output_tokens / len(final_results):.1f}")
            print(f"   • Total tokens: {(self.total_input_tokens + self.total_output_tokens) / len(final_results):.1f}")
        
        print("=" * 70)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit bug detection findings with LLM assistance.")
    parser.add_argument("--input-csv", required=True, help="Path to the bug detection CSV file.")
    parser.add_argument("--repo", required=True, help="Path to the local git repository containing the patches.")
    parser.add_argument("--source-dir", help="Path to source tree for CodeSearcher (defaults to repo path).")
    parser.add_argument("--output-json", help="Path to write detailed JSON results.")
    parser.add_argument("--output-csv", help="Optional path to write a summary CSV.")
    parser.add_argument("--limit", type=int, help="Limit number of rows to audit.")
    parser.add_argument("--only-violations", action="store_true", help="Process only rows with has_violation==True.")
    parser.add_argument("--max-more-context", type=int, default=5, help="Maximum additional context rounds per finding.")
    parser.add_argument("--workers", type=int, default=4, help="Number of worker threads to use.")
    parser.add_argument("--request-timeout", type=float, default=60.0, help="LLM request timeout in seconds.")
    parser.add_argument("--max-retries", type=int, default=5, help="Maximum LLM retry attempts per request.")
    parser.add_argument("--model", type=str, default="gpt-4o-mini", help="LLM model name to use (from openai_config.yaml).")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    model_name = args.model.replace("/", "_").replace("-", "_")
    output_json = args.output_json or f"bug_detection_audit_results_{model_name}_{timestamp}.json"
    output_csv = args.output_csv

    auditor = BugDetectionAuditor(
        repo_path=args.repo,
        source_dir=args.source_dir,
        max_more_context=args.max_more_context,
        max_workers=args.workers,
        llm_timeout=args.request_timeout,
        llm_max_retries=args.max_retries,
        llm_model=args.model,
    )

    auditor.process(
        input_csv=args.input_csv,
        output_json=output_json,
        output_csv=output_csv,
        limit=args.limit,
        only_violations=args.only_violations,
    )


if __name__ == "__main__":
    main()
