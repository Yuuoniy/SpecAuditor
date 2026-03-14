#!/usr/bin/env python3
"""Reviewer-facing reproduced bug-detection benchmark runner."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from artifact.common import ensure_env, ensure_git_safe_directory, write_summary
except ImportError:
    from common import ensure_env, ensure_git_safe_directory, write_summary  # type: ignore

from scripts.bug_detection_threaded import ThreadedBugDetector

WORKFLOW_DIR = Path(__file__).resolve().parent
DATASET = WORKFLOW_DIR / "datasets" / "checks.csv"
REFERENCE_RESULTS = WORKFLOW_DIR / "reference" / "reference.csv"
GENERATION_SEEDS = ROOT / "artifact" / "reproduced_generation" / "datasets" / "seed_commits.csv"


def normalize_bool(value) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes"}


def evaluate_bug_check(detector: ThreadedBugDetector, row_dict: dict, thread_id: int) -> dict:
    target_function = str(row_dict["detected target"]).strip()
    buggy_function = str(row_dict["buggy function"]).strip()
    match_code = detector.code_searcher.query_given_func_code(buggy_function)

    if not match_code:
        return {
            **row_dict,
            "_row_index": row_dict["_row_index"],
            "has_violation": False,
            "confidence": "NO_MATCH",
            "analysis": "The buggy function could not be localized in the current kernel tree.",
        }

    if f"{target_function}(" not in match_code:
        return {
            **row_dict,
            "_row_index": row_dict["_row_index"],
            "has_violation": False,
            "confidence": "TARGET_NOT_FOUND",
            "analysis": "The localized buggy function does not contain the entity call in the current kernel tree.",
        }

    analysis_result = detector.analyze_code_violation_worker(
        ((buggy_function, match_code), row_dict["spec_predicate"], target_function, thread_id)
    )
    return {
        **row_dict,
        "_row_index": row_dict["_row_index"],
        "has_violation": bool(analysis_result.get("is_violation", False)),
        "confidence": analysis_result.get("confidence", ""),
        "analysis": analysis_result.get("analysis", ""),
    }


def run_reproduced_bug_detection(kernel_path: str, output_dir: str, model: str, max_workers: int):
    ensure_env(model)
    ensure_git_safe_directory(kernel_path)

    output_dir_path = Path(output_dir)
    output_dir_path.mkdir(parents=True, exist_ok=True)

    checks_df = pd.read_csv(DATASET).fillna("")
    for col in checks_df.columns:
        checks_df[col] = checks_df[col].astype(str).str.strip()

    detector = ThreadedBugDetector(
        kernel_path,
        model=model,
        max_matches_to_analyze=20,
        max_workers=max_workers,
        candidate_function_allowlist=sorted(checks_df["buggy function"].astype(str).unique().tolist()),
    )

    print(f"Loaded {len(checks_df)} bug-check rows across {checks_df['seed patch'].nunique()} seed patches")
    print(f"Running reproduced bug detection with {max_workers} workers")

    records = []
    partial_csv = output_dir_path / "reproduced_bug_detection_results.partial.csv"
    row_dicts = checks_df.reset_index().rename(columns={"index": "_row_index"}).to_dict("records")
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_index = {
            executor.submit(evaluate_bug_check, detector, row_dict, idx % max_workers): idx
            for idx, row_dict in enumerate(row_dicts)
        }

        for completed_idx, future in enumerate(as_completed(future_to_index), 1):
            row_idx = future_to_index[future]
            row_dict = row_dicts[row_idx]
            result = future.result()
            records.append(result)

            violation = "VIOLATION" if normalize_bool(result["has_violation"]) else "OK"
            print(
                f"[{completed_idx}/{len(row_dicts)}] "
                f"{row_dict['seed patch']} :: {row_dict['detected target']} :: {row_dict['buggy function']} -> {violation}"
            )

            if completed_idx % 10 == 0 or completed_idx == len(row_dicts):
                pd.DataFrame(records).sort_values("_row_index").drop(columns=["_row_index"]).to_csv(partial_csv, index=False)

    result_df = pd.DataFrame(records).sort_values("_row_index").drop(columns=["_row_index"])
    result_csv = output_dir_path / "reproduced_bug_detection_results.csv"
    result_df.to_csv(result_csv, index=False)

    per_seed_summary = (
        result_df.groupby("seed patch")
        .agg(
            evaluated_bug_rows=("buggy function", "size"),
            detected_bug_rows=("has_violation", lambda values: int(sum(normalize_bool(v) for v in values))),
        )
        .reset_index()
    )
    summary_csv = output_dir_path / "reproduced_bug_detection_summary.csv"
    per_seed_summary.to_csv(summary_csv, index=False)

    reference_df = pd.read_csv(REFERENCE_RESULTS).fillna("")
    reference_detected = int(sum(normalize_bool(v) for v in reference_df["has_violation"].tolist()))
    generation_seed_count = int(pd.read_csv(GENERATION_SEEDS).shape[0]) if GENERATION_SEEDS.exists() else None
    summary = {
        "benchmark_bug_rows": int(len(checks_df)),
        "evaluated_bug_rows": int(len(result_df)),
        "detected_bug_rows": int(sum(normalize_bool(v) for v in result_df["has_violation"].tolist())),
        "reference_detected_bug_rows": reference_detected,
        "seed_patch_count": int(result_df["seed patch"].nunique()),
        "reference_files": {
            "benchmark_dataset": str(DATASET),
            "reference_results": str(REFERENCE_RESULTS),
        },
        "per_seed_detected_bug_rows": {
            row["seed patch"]: {
                "evaluated_bug_rows": int(row["evaluated_bug_rows"]),
                "detected_bug_rows": int(row["detected_bug_rows"]),
            }
            for row in per_seed_summary.to_dict("records")
        },
    }
    if generation_seed_count is not None:
        summary["generation_seed_count"] = generation_seed_count
    summary_path = output_dir_path / "reproduced_bug_detection_summary.json"
    write_summary(summary_path, summary)

    return {
        "result_csv": str(result_csv),
        "summary_csv": str(summary_csv),
        "summary_json": str(summary_path),
        "summary": summary,
    }


def main():
    parser = argparse.ArgumentParser(description="Run the reproduced bug-detection benchmark")
    parser.add_argument("--kernel-path", default="/root/linux", help="Linux kernel repository to analyze")
    parser.add_argument(
        "--output-dir",
        default=str(ROOT / "artifact" / "results" / "reproduced_bug_detection"),
        help="Directory for generated outputs",
    )
    parser.add_argument("--model", default="claude-sonnet-4-20250514", help="LLM model for bug analysis")
    parser.add_argument("--max-workers", type=int, default=4, help="Worker count for LLM-based bug analysis")
    args = parser.parse_args()

    result = run_reproduced_bug_detection(
        kernel_path=args.kernel_path,
        output_dir=args.output_dir,
        model=args.model,
        max_workers=args.max_workers,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
