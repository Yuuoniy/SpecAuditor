#!/usr/bin/env python3
"""
Complete Specification Validator
Validates complete specifications (target + predicate) extracted from spec_extract.py

Core validation logic:
1. Use PatchFileExtractor to extract before/after code to directories
2. Specification Checking: Use LLM to analyze before/after code                return {
                    "hexsha": hexsha,
                    "target": target,
                    "predicate": predicate,
                    "validation_status": "error",
                    "specification_valid": False,
                    "before_patch_reasoning": "Cannot extract patch files",
                    "after_patch_reasoning": "Cannot extract patch files",
                    "summary_reasoning": "Cannot extract patch files"
                } complete specification
3. Validates both target and predicate together as a unified specification
"""

import argparse
import pandas as pd
import json
import sys
import os
from pathlib import Path
from datetime import datetime
import logging

try:
    from scripts.utils.artifact_utils import configure_script_imports
except ImportError:
    from utils.artifact_utils import configure_script_imports

configure_script_imports(__file__)

from openai_client import OpenAIClient
from patch_file_extractor import PatchFileExtractor
from pydriller import Git

from spec_validate_prompts import ANALYZE_VIOLATION_SYSTEM, ANALYZE_VIOLATION_USER

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Suppress HTTP request logs from OpenAI client and other libraries
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("openai").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


class SpecValidator:

    def __init__(
        self,
        kernel_path="./linux",
        patch_files_dir="spec_validation_patch_files",
        target_col="target",
        predicate_col="predicate",
        llm_config_path=None,
    ):
        self.kernel_path = kernel_path
        self.patch_files_dir = patch_files_dir
        self.results = []

        self.target_col = target_col
        self.predicate_col = predicate_col

        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.api_calls_count = 0

        self.git_repo = Git(kernel_path)

        self.llm_config_path = Path(llm_config_path).expanduser() if llm_config_path else None
        self.llm_config = self._load_llm_config(self.llm_config_path)
        self.llm_client = self._create_llm_client()
        self.active_model = self.llm_client.get_config().get("model", "unknown")

    def _load_llm_config(self, config_path):
        if not config_path:
            return {}

        try:
            with open(config_path, "r", encoding="utf-8") as config_file:
                data = json.load(config_file)
        except FileNotFoundError:
            logger.warning(f"LLM config file not found: {config_path}")
            return {}
        except json.JSONDecodeError as exc:
            logger.error(f"Failed to parse LLM config file {config_path}: {exc}")
            return {}
        except Exception as exc:
            logger.error(f"Unexpected error reading LLM config {config_path}: {exc}")
            return {}

        if not isinstance(data, dict):
            logger.error(f"LLM config file must contain a JSON object, got {type(data)}")
            return {}

        return data

    def _create_llm_client(self):
        config = self.llm_config or {}
        client_kwargs = {}

        api_key = config.get("api_key") or config.get("apikey")
        base_url = config.get("base_url") or config.get("baseurl")
        model = config.get("model") or config.get("model_name") or config.get("modelname")
        temperature = config.get("temperature")
        max_tokens = config.get("max_tokens")

        if api_key:
            client_kwargs["api_key"] = api_key
        if base_url:
            client_kwargs["base_url"] = base_url
        if model:
            client_kwargs["model"] = model
        if temperature is not None:
            client_kwargs["temperature"] = temperature
        if max_tokens is not None:
            client_kwargs["max_tokens"] = max_tokens

        if client_kwargs:
            safe_log = {k: ("***" if "key" in k.lower() else v) for k, v in client_kwargs.items()}
            source = str(self.llm_config_path) if self.llm_config_path else "inline config"
            logger.info(f"Using LLM overrides from {source}: {safe_log}")

        try:
            return OpenAIClient(**client_kwargs)
        except Exception as exc:
            logger.error(f"Failed to initialize OpenAIClient with overrides: {exc}. Falling back to defaults.")
            return OpenAIClient()
    
    def get_model_name(self):
        return self.active_model
    
    def _parse_llm_result(self, llm_response):
        """Parse LLM response result - handle both JSON and text formats"""
        try:
            # First try to parse as JSON
            import json
            import re
            
            # Clean the response to extract JSON
            response_clean = llm_response.strip()
            
            # Try to find JSON in the response
            json_match = re.search(r'\{.*\}', response_clean, re.DOTALL)
            if json_match:
                json_str = json_match.group()
                parsed = json.loads(json_str)
                
                decision = parsed.get("decision", "").upper()
                reasoning = parsed.get("reasoning", llm_response)
                confidence = parsed.get("confidence", "LOW").upper()
                
                has_violation = False
                if "YES" in decision:
                    has_violation = True
                elif "UNCERTAIN" in decision:
                    has_violation = True
                
                return {
                    "has_violation": has_violation,
                    "reasoning": reasoning,
                    "confidence": confidence,
                    "raw_response": llm_response
                }
            
            response_upper = llm_response.upper().strip()
            
            has_violation = False
            if "YES" in response_upper:
                has_violation = True
            elif "UNCERTAIN" in response_upper:
                has_violation = True
            
            confidence = "LOW"
            if "HIGH" in response_upper:
                confidence = "HIGH"
            elif "MEDIUM" in response_upper:
                confidence = "MEDIUM"
            
            return {
                "has_violation": has_violation,
                "reasoning": llm_response.strip(),
                "confidence": confidence,
                "raw_response": llm_response
            }
        except Exception as e:
            logger.warning(f"Failed to parse LLM result: {e}")
            return {
                "has_violation": False,
                "reasoning": f"Parse failed: {str(e)}",
                "confidence": "LOW",
                "raw_response": llm_response
            }
    
    def extract_patch_files(self, hexsha):
        try:
            extractor = PatchFileExtractor(self.git_repo, hexsha, self.patch_files_dir)
            files = extractor.extract_modified_files()
            
            if files:
                before_dir = extractor.before_dir
                after_dir = extractor.after_dir
                return before_dir, after_dir, files
            else:
                print("      ⚠️  No files extracted")
                return None, None, []
                
        except Exception as e:
            logger.error(f"Failed to extract patch files: {e}")
            print(f"      ❌ Extraction failed: {str(e)[:50]}...")
            return None, None, []
    
    def check_specification_with_llm(self, target, predicate, hexsha):
        """Complete specification validation: use LLM to analyze before/after functions against full specification"""
        try:
            # Use pydriller to get function-level changes directly
            function_changes = self._get_function_changes_from_commit(hexsha)
            
            if not function_changes:
                print("      ⚠️  No function-level changes found in patch")
                return {
                    "specification_valid": False,
                    "function_changes_count": 0,
                    "valid_changes_count": 0,
                    "reasoning": "No function-level changes found in patch"
                }
            
            print(f"      📊 Found {len(function_changes)} function changes")
            
            complete_specification = f"Target: {target}\nPredicate: {predicate}"
            print(f"      📋 Complete specification: {complete_specification[:100]}{'...' if len(complete_specification) > 100 else ''}")
            
            # Analyze each function change and store detailed results
            valid_changes = 0
            total_changes = len(function_changes)
            function_analysis_results = []
            
            for i, change in enumerate(function_changes, 1):
                func_name = change['function_name']
                before_code = change['before_code']
                after_code = change['after_code']
                
                print(f"      📋 Analyzing function {i}/{total_changes}: {func_name}")
                
                # Analyze before version against complete specification
                before_result = None
                if before_code:
                    print(f"        📖 Analyzing pre-patch code against specification...")
                    before_result = self._analyze_specification_violation(
                        complete_specification, before_code, f"pre_patch_{func_name}"
                    )
                    print(f"        📊 Pre-patch violation: {before_result['has_violation'] if before_result else 'N/A'}")
                    if before_result:
                        print(f"        🎯 Pre-patch confidence: {before_result['confidence']}")
                        if len(before_result['reasoning']) > 120:
                            print(f"        💭 Pre-patch reasoning: {before_result['reasoning'][:120]}...")
                        else:
                            print(f"        💭 Pre-patch reasoning: {before_result['reasoning']}")
                
                # Analyze after version against complete specification
                after_result = None
                if after_code:
                    print(f"        📖 Analyzing post-patch code against specification...")
                    after_result = self._analyze_specification_violation(
                        complete_specification, after_code, f"post_patch_{func_name}"
                    )
                    print(f"        📊 Post-patch violation: {after_result['has_violation'] if after_result else 'N/A'}")
                    if after_result:
                        print(f"        🎯 Post-patch confidence: {after_result['confidence']}")
                        if len(after_result['reasoning']) > 120:
                            print(f"        💭 Post-patch reasoning: {after_result['reasoning'][:120]}...")
                        else:
                            print(f"        💭 Post-patch reasoning: {after_result['reasoning']}")
                
                # Store detailed analysis results for this function
                function_analysis = {
                    'function_name': func_name,
                    'file_path': change.get('file_path', ''),
                    'pre_patch_analysis': before_result,
                    'post_patch_analysis': after_result,
                    'has_pre_patch_code': before_code is not None,
                    'has_post_patch_code': after_code is not None
                }
                
                # Check if this function change validates the complete specification
                has_before_violation = before_result and before_result['has_violation']
                has_after_violation = after_result and after_result['has_violation']
                
                if has_before_violation and not has_after_violation:
                    valid_changes += 1
                    function_analysis['validation_result'] = 'validates_spec'
                    print(f"        ✅ Function {func_name}: Fix validates specification")
                    print(f"           ✨ Had violation before fix, no violation after fix")
                elif not has_before_violation and not has_after_violation:
                    function_analysis['validation_result'] = 'no_violations_detected'
                    print(f"        ⚠️  Function {func_name}: No violations detected in either version")
                    print(f"           🤔 Specification may not apply to this function")
                elif has_before_violation and has_after_violation:
                    function_analysis['validation_result'] = 'violation_persists'
                    print(f"        ❌ Function {func_name}: Violation still exists after fix")
                    print(f"           🔴 Fix incomplete or specification still violated")
                elif not has_before_violation and has_after_violation:
                    function_analysis['validation_result'] = 'new_violation_introduced'
                    print(f"        ⚠️  Function {func_name}: New violation introduced")
                    print(f"           🚨 Possible regression or false positive")
                else:
                    function_analysis['validation_result'] = 'indeterminate'
                    print(f"        ❓ Function {func_name}: Unable to determine validation status")
                
                function_analysis_results.append(function_analysis)
                print(f"        {'-'*60}")  # Separator between functions
            
            print(f"\n    📊 Specification Validation Summary:")
            print(f"       Total functions analyzed: {total_changes}")
            print(f"       Valid specification fixes: {valid_changes}")
            if total_changes > 0:
                print(f"       Success rate: {valid_changes/total_changes*100:.1f}%")
            else:
                print(f"       Success rate: N/A (no functions found)")
            
            if valid_changes > 0:
                specification_valid = True
                summary_reasoning = f"{valid_changes}/{total_changes} function changes validate the complete specification"
            else:
                specification_valid = False
                summary_reasoning = f"None of the {total_changes} function changes validate the complete specification"
            
            return {
                "specification_valid": specification_valid,
                "function_changes_count": total_changes,
                "valid_changes_count": valid_changes,
                "summary_reasoning": summary_reasoning,
                "function_analysis_details": function_analysis_results
            }
            
        except Exception as e:
            logger.error(f"Error in specification validation: {e}")
            return {
                "specification_valid": False,
                "function_changes_count": 0,
                "valid_changes_count": 0,
                "summary_reasoning": f"Error during specification validation: {str(e)}",
                "function_analysis_details": []
            }
    
    def validate_single_specification(self, row):
        hexsha = row.get('hexsha', '')
        predicate = row.get(self.predicate_col, '')
        description = row.get('description', '')
        target = row.get(self.target_col, '')

        print(f"\n🔍 Validating complete specification for: {hexsha[:8]}")
        print(f"   Target: {target[:60]}{'...' if len(target) > 60 else ''}")
        print(f"   Predicate: {predicate[:60]}{'...' if len(predicate) > 60 else ''}")

        if not hexsha or not predicate or not target:
            logger.warning(f"Missing required fields: hexsha={bool(hexsha)}, target={bool(target)}, predicate={bool(predicate)}")
            print("   ❌ Missing required fields")
            return {
                "hexsha": hexsha,
                "target": target,
                "predicate": predicate,
                "model_name": self.get_model_name(),
                "validation_status": "error",
                "specification_valid": False,
                "before_patch_reasoning": "Missing required input fields (target, predicate, or hexsha)",
                "after_patch_reasoning": "Missing required input fields (target, predicate, or hexsha)",
                "summary_reasoning": "Missing required input fields (target, predicate, or hexsha)"
            }

        try:
            print("   📁 Extracting patch files...")
            before_dir, after_dir, files = self.extract_patch_files(hexsha)

            if not before_dir or not after_dir:
                print("   ❌ Failed to extract patch files")
                return {
                    "hexsha": hexsha,
                    "target": target,
                    "predicate": predicate,
                    "model_name": self.get_model_name(),
                    "validation_status": "error",
                    "predicate_valid": False,
                    "reasoning": "Cannot extract patch files"
                }

            print(f"      ✅ Extracted {len(files)} files")

            print("   🔍 Validating complete specification...")
            spec_result = self.check_specification_with_llm(target, predicate, hexsha)
            print(spec_result)
            print(f"      Specification valid: {spec_result['specification_valid']}")
            if 'function_changes_count' in spec_result:
                print(f"      Function changes analyzed: {spec_result['function_changes_count']}")
                print(f"      Valid changes: {spec_result.get('valid_changes_count', 0)}")

            specification_valid = spec_result['specification_valid']

            if specification_valid:
                validation_status = "passed"
                print("   ✅ PASSED - Specification validated")
            else:
                validation_status = "failed"
                print("   ❌ FAILED - Specification validation failed")

            # Extract aggregated reasoning from function analysis
            before_reasons = []
            after_reasons = []

            for func_detail in spec_result.get('function_analysis_details', []):
                pre_analysis = func_detail.get('pre_patch_analysis', {})
                post_analysis = func_detail.get('post_patch_analysis', {})

                if pre_analysis and func_detail.get('has_pre_patch_code'):
                    before_reasons.append(f"{func_detail.get('function_name', 'unknown')}: {pre_analysis.get('reasoning', 'N/A')}")

                if post_analysis and func_detail.get('has_post_patch_code'):
                    after_reasons.append(f"{func_detail.get('function_name', 'unknown')}: {post_analysis.get('reasoning', 'N/A')}")

            aggregated_before_reasoning = " | ".join(before_reasons) if before_reasons else "No pre-patch analysis available"
            aggregated_after_reasoning = " | ".join(after_reasons) if after_reasons else "No post-patch analysis available"

            complete_result = {
                "hexsha": hexsha,
                "target": target,
                "predicate": predicate,
                "description": description,
                "model_name": self.get_model_name(),
                "validation_status": validation_status,
                "specification_valid": specification_valid,
                "before_patch_reasoning": aggregated_before_reasoning,
                "after_patch_reasoning": aggregated_after_reasoning,
                **spec_result,
                "extracted_files_count": len(files)
            }

            return complete_result

        except Exception as e:
            logger.error(f"Validation process error: {e}")
            print(f"   💥 ERROR - Validation failed: {str(e)[:50]}...")
            return {
                "hexsha": hexsha,
                "target": target,
                "predicate": predicate,
                "model_name": self.get_model_name(),
                "validation_status": "error",
                "specification_valid": False,
                "before_patch_reasoning": f"Error: {str(e)}",
                "after_patch_reasoning": f"Error: {str(e)}",
                "summary_reasoning": f"Validation process error: {str(e)}"
            }
    
    def validate_csv(self, input_csv, output_csv=None, hexsha_filter=None):
        logger.info(f"Reading specification extraction results: {input_csv}")

        try:
            df = pd.read_csv(input_csv)
        except Exception as e:
            logger.error(f"Failed to read CSV file: {e}")
            return

        required_columns = ['hexsha', self.target_col, self.predicate_col]
        missing_columns = [col for col in required_columns if col not in df.columns]

        if missing_columns:
            logger.error(f"Missing required columns: {missing_columns}")
            logger.info(f"Available columns: {list(df.columns)}")
            return

        valid_df = df[
            (df[self.predicate_col].notna()) & (df[self.predicate_col] != '') &
            (df[self.target_col].notna()) & (df[self.target_col] != '')
        ]

        # Filter by specific hexsha list if provided
        if hexsha_filter:
            print(f"🔍 Filtering to specific hexshas: {len(hexsha_filter)} provided")
            original_count = len(valid_df)
            valid_df = valid_df[valid_df['hexsha'].isin(hexsha_filter)]
            filtered_count = len(valid_df)
            print(f"📊 Filtered from {original_count} to {filtered_count} specifications")
            
            # Check if any requested hexshas were not found
            found_hexshas = set(valid_df['hexsha'].unique())
            requested_hexshas = set(hexsha_filter)
            missing_hexshas = requested_hexshas - found_hexshas
            if missing_hexshas:
                print(f"⚠️  Warning: {len(missing_hexshas)} requested hexshas not found in CSV:")
                for hexsha in sorted(missing_hexshas):
                    print(f"   - {hexsha}")

        if len(valid_df) == 0:
            print("No valid complete specifications to validate")
            return

        if not output_csv:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            base_name = os.path.basename(input_csv).replace('.csv', '')
            output_csv = f"spec_validation_{base_name}_{timestamp}.csv"

        print(f"\n🚀 Starting batch specification validation of {len(valid_df)} complete specifications...")
        print(f"📄 Results will be saved to: {output_csv}")
        print("=" * 80)

        passed_count = 0
        failed_count = 0
        error_count = 0

        for idx, (_, row) in enumerate(valid_df.iterrows(), 1):
            hexsha = row.get('hexsha', 'N/A')[:8]
            progress = (idx / len(valid_df)) * 100

            print(f"\n[{idx:3d}/{len(valid_df)}] ({progress:5.1f}%) Processing {hexsha}...")

            try:
                result = self.validate_single_specification(row)
                self.results.append(result)

                status = result.get('validation_status', 'unknown')

                if status == 'passed':
                    passed_count += 1
                elif status == 'failed':
                    failed_count += 1
                else:
                    error_count += 1

            except Exception as e:
                logger.error(f"Validation failed {hexsha}: {e}")
                print(f"   💥 EXCEPTION - {str(e)[:50]}...")
                error_result = {
                    "hexsha": row.get('hexsha', ''),
                    "target": row.get(self.target_col, ''),
                    "predicate": row.get(self.predicate_col, ''),
                    "model_name": self.get_model_name(),
                    "validation_status": "error",
                    "specification_valid": False,
                    "before_patch_reasoning": f"Processing error: {str(e)}",
                    "after_patch_reasoning": f"Processing error: {str(e)}",
                    "summary_reasoning": f"Processing error: {str(e)}"
                }
                self.results.append(error_result)
                error_count += 1

            # Show progress summary every 5 items or at the end
            if idx % 5 == 0 or idx == len(valid_df):
                print(f"\n📊 Progress Summary:")
                print(f"   Processed: {idx}/{len(valid_df)} ({progress:.1f}%)")
                print(f"   ✅ Passed: {passed_count} | ❌ Failed: {failed_count} | 💥 Errors: {error_count}")
                print("-" * 60)

        self.save_results(output_csv)

        print(f"\n🎉 Specification Validation Complete!")
        print("=" * 80)
        print(f"📊 Final Results:")
        print(f"   Total processed: {len(self.results)}")
        print(f"   ✅ Passed: {passed_count} ({passed_count/len(self.results)*100:.1f}%)")
        print(f"   ❌ Failed: {failed_count} ({failed_count/len(self.results)*100:.1f}%)")
        print(f"   💥 Errors: {error_count} ({error_count/len(self.results)*100:.1f}%)")
        print(f"\n📄 Results saved to: {output_csv}")

        self.print_token_statistics()
    
    def save_results(self, output_file):
        try:
            # Save complete raw results with all LLM judgments
            raw_results_file = output_file.replace('.csv', '_raw_complete.json')
            self._save_raw_complete_results(raw_results_file)
            
            # Save simplified CSV for easy analysis
            simplified_csv = output_file.replace('.csv', '_simplified.csv')
            self._save_simplified_results(simplified_csv)
            
            # Save the main results CSV (backward compatibility)
            results_df = pd.DataFrame(self.results)
            results_df.to_csv(output_file, index=False, encoding='utf-8')
            logger.info(f"Main results saved to: {output_file}")
            
            detailed_results_file = output_file.replace('.csv', '_detailed_analysis.csv')
            self._save_detailed_function_analysis(detailed_results_file)
            
            summary_file = output_file.replace('.csv', '_summary.txt')
            with open(summary_file, 'w') as f:
                f.write("Complete Specification Validation Summary\n")
                f.write("=" * 80 + "\n\n")
                f.write(f"Model name: {self.get_model_name()}\n")
                if self.llm_config_path:
                    f.write(f"Config file: {self.llm_config_path}\n")
                f.write("\n")
                
                total = len(self.results)
                passed = sum(1 for r in self.results if r.get('validation_status') == 'passed')
                failed = sum(1 for r in self.results if r.get('validation_status') == 'failed')
                errors = sum(1 for r in self.results if r.get('validation_status') == 'error')
                
                f.write("Overall Results:\n")
                f.write(f"  Total processed: {total}\n")
                f.write(f"  Passed: {passed} ({passed/total*100:.1f}%)\n")
                f.write(f"  Failed: {failed} ({failed/total*100:.1f}%)\n")
                f.write(f"  Errors: {errors} ({errors/total*100:.1f}%)\n\n")
                
                valid_cases = [r for r in self.results if r.get('validation_status') != 'error']
                if valid_cases:
                    total_functions = sum(r.get('function_changes_count', 0) for r in valid_cases)
                    total_valid_changes = sum(r.get('valid_changes_count', 0) for r in valid_cases)
                    
                    f.write("Function-level Analysis:\n")
                    f.write(f"  Total function changes analyzed: {total_functions}\n")
                    f.write(f"  Total valid specification fixes: {total_valid_changes}\n")
                    if total_functions > 0:
                        f.write(f"  Function-level success rate: {total_valid_changes/total_functions*100:.1f}%\n\n")
                    else:
                        f.write(f"  Function-level success rate: N/A\n\n")
                
                f.write("Token Usage Statistics:\n")
                f.write(f"  Total API calls: {self.api_calls_count}\n")
                f.write(f"  Total input tokens: {self.total_input_tokens:,}\n")
                f.write(f"  Total output tokens: {self.total_output_tokens:,}\n")
                f.write(f"  Total tokens: {self.total_input_tokens + self.total_output_tokens:,}\n")
                if self.api_calls_count > 0:
                    f.write(f"  Average input tokens per call: {self.total_input_tokens / self.api_calls_count:.1f}\n")
                    f.write(f"  Average output tokens per call: {self.total_output_tokens / self.api_calls_count:.1f}\n")
                f.write("=" * 80 + "\n")
            
            logger.info(f"Comprehensive summary saved to: {summary_file}")
                
        except Exception as e:
            logger.error(f"Failed to save results: {e}")
    
    def _save_raw_complete_results(self, output_file):
        """Save complete raw results including all LLM judgments to JSON"""
        try:
            complete_data = {
                "validation_results": self.results,
                "token_statistics": {
                    "total_api_calls": self.api_calls_count,
                    "total_input_tokens": self.total_input_tokens,
                    "total_output_tokens": self.total_output_tokens,
                    "total_tokens": self.total_input_tokens + self.total_output_tokens
                },
                "metadata": {
                    "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "total_specifications": len(self.results),
                    "kernel_path": self.kernel_path,
                    "model_name": self.get_model_name(),
                    "config_file": str(self.llm_config_path) if self.llm_config_path else None,
                }
            }
            
            with open(output_file, 'w', encoding='utf-8') as f:
                json.dump(complete_data, f, indent=2, ensure_ascii=False)
            
            logger.info(f"Complete raw results with all LLM judgments saved to: {output_file}")
            
        except Exception as e:
            logger.error(f"Failed to save raw complete results: {e}")
    
    def _save_simplified_results(self, output_file):
        try:
            simplified_rows = []
            
            for result in self.results:
                # Extract key information for simplified view
                simplified_row = {
                    'hexsha': result.get('hexsha', ''),
                    'target': result.get('target', ''),
                    'predicate': result.get('predicate', ''),
                    'model_name': result.get('model_name', self.get_model_name()),
                    'validation_status': result.get('validation_status', ''),
                    'specification_valid': result.get('specification_valid', False),
                    'function_changes_count': result.get('function_changes_count', 0),
                    'valid_changes_count': result.get('valid_changes_count', 0),
                    'before_patch_reasoning': result.get('before_patch_reasoning', ''),
                    'after_patch_reasoning': result.get('after_patch_reasoning', ''),
                    'summary_reasoning': result.get('summary_reasoning', ''),
                    'input_tokens': result.get('input_tokens', 0),
                    'output_tokens': result.get('output_tokens', 0),
                    'total_tokens': result.get('total_tokens', 0),
                    'api_calls': result.get('api_calls', 0)
                }
                
                simplified_rows.append(simplified_row)
            
            simplified_df = pd.DataFrame(simplified_rows)
            simplified_df.to_csv(output_file, index=False, encoding='utf-8')
            logger.info(f"Simplified results saved to: {output_file}")
            
        except Exception as e:
            logger.error(f"Failed to save simplified results: {e}")
    
    def _save_detailed_function_analysis(self, output_file):
        try:
            detailed_rows = []
            
            for result in self.results:
                hexsha = result.get('hexsha', '')
                target = result.get('target', '')
                predicate = result.get('predicate', '')
                validation_status = result.get('validation_status', '')
                
                function_details = result.get('function_analysis_details', [])
                
                if not function_details:
                    # If no function analysis details, create a row with basic info
                    detailed_rows.append({
                        'hexsha': hexsha,
                        'target': target,
                        'predicate': predicate,
                        'model_name': result.get('model_name', self.get_model_name()),
                        'validation_status': validation_status,
                        'function_name': 'N/A',
                        'file_path': 'N/A',
                        'validation_result': 'N/A',
                        'has_pre_patch_code': False,
                        'has_post_patch_code': False,
                        'pre_patch_has_violation': 'N/A',
                        'pre_patch_confidence': 'N/A',
                        'pre_patch_reasoning': 'No function analysis available',
                        'post_patch_has_violation': 'N/A',
                        'post_patch_confidence': 'N/A',
                        'post_patch_reasoning': 'No function analysis available'
                    })
                else:
                    # Create a row for each function analyzed
                    for func_detail in function_details:
                        pre_analysis = func_detail.get('pre_patch_analysis') or {}
                        post_analysis = func_detail.get('post_patch_analysis') or {}
                        
                        detailed_rows.append({
                            'hexsha': hexsha,
                            'target': target,
                            'predicate': predicate,
                            'model_name': result.get('model_name', self.get_model_name()),
                            'validation_status': validation_status,
                            'function_name': func_detail.get('function_name', ''),
                            'file_path': func_detail.get('file_path', ''),
                            'validation_result': func_detail.get('validation_result', ''),
                            'has_pre_patch_code': func_detail.get('has_pre_patch_code', False),
                            'has_post_patch_code': func_detail.get('has_post_patch_code', False),
                            'pre_patch_has_violation': pre_analysis.get('has_violation', 'N/A'),
                            'pre_patch_confidence': pre_analysis.get('confidence', 'N/A'),
                            'pre_patch_reasoning': pre_analysis.get('reasoning', 'N/A'),
                            'pre_patch_input_tokens': pre_analysis.get('input_tokens', 0),
                            'pre_patch_output_tokens': pre_analysis.get('output_tokens', 0),
                            'post_patch_has_violation': post_analysis.get('has_violation', 'N/A'),
                            'post_patch_confidence': post_analysis.get('confidence', 'N/A'),
                            'post_patch_reasoning': post_analysis.get('reasoning', 'N/A'),
                            'post_patch_input_tokens': post_analysis.get('input_tokens', 0),
                            'post_patch_output_tokens': post_analysis.get('output_tokens', 0)
                        })
            
            if detailed_rows:
                detailed_df = pd.DataFrame(detailed_rows)
                detailed_df.to_csv(output_file, index=False, encoding='utf-8')
                logger.info(f"Detailed function analysis saved to: {output_file}")
            else:
                logger.warning("No detailed function analysis data to save")
                
        except Exception as e:
            logger.error(f"Failed to save detailed function analysis: {e}")
    
    def print_config(self):
        print("Complete Specification Validator Configuration:")
        print("=" * 50)
        print(f"  Kernel path: {self.kernel_path}")
        print(f"  Patch files directory: {self.patch_files_dir}")
        print(f"  LLM config file: {self.llm_config_path if self.llm_config_path else 'Not set'}")
        config = self.llm_client.get_config()
        print(f"  API URL: {config.get('base_url', 'N/A')}")
        print(f"  Model: {config.get('model', 'N/A')}")
        print(f"  Temperature: {config.get('temperature', 'N/A')}")
        print("=" * 50)
    
    def _get_function_changes_from_commit(self, hexsha):
        """Get code changes from commit - handles both functions and other code structures"""
        try:
            from pydriller import Repository, ModificationType
            
            code_changes = []
            
            for commit in Repository(self.kernel_path, single=hexsha).traverse_commits():
                for modified_file in commit.modified_files:
                    if modified_file.change_type != ModificationType.MODIFY:
                        continue
                    if not (modified_file.filename.endswith('.c') or modified_file.filename.endswith('.h')):
                        continue
                    
                    found_changes = False
                    
                    # Strategy 1: Try to get changed methods/functions first
                    if modified_file.changed_methods:
                        for method in modified_file.changed_methods:
                            # Extract function code from source using line numbers
                            before_code = self._extract_function_by_lines(
                                modified_file.source_code_before, 
                                method.start_line, 
                                method.end_line
                            ) if modified_file.source_code_before else None
                            
                            after_code = self._extract_function_by_lines(
                                modified_file.source_code, 
                                method.start_line, 
                                method.end_line
                            ) if modified_file.source_code else None
                            
                            # Only add if we have meaningful changes
                            if before_code or after_code:
                                code_changes.append({
                                    'function_name': method.name,
                                    'file_path': modified_file.filename,
                                    'before_code': before_code,
                                    'after_code': after_code,
                                    'change_type': 'function',
                                    'start_line': method.start_line,
                                    'end_line': method.end_line
                                })
                                found_changes = True
                    
                    # Strategy 2: If no function changes found, analyze the diff for other changes
                    if not found_changes:
                        diff_changes = self._extract_diff_based_changes(modified_file)
                        if diff_changes:
                            code_changes.extend(diff_changes)
                            found_changes = True
            
            return code_changes
            
        except Exception as e:
            logger.error(f"Error getting code changes from commit {hexsha}: {e}")
            return []
    
    def _extract_function_by_lines(self, source_code, start_line, end_line):
        try:
            if not source_code or not start_line or not end_line:
                return None
            
            lines = source_code.split('\n')
            # Convert to 0-based indexing and ensure valid range
            start_idx = max(0, start_line - 1)
            end_idx = min(len(lines), end_line)
            
            if start_idx >= len(lines) or start_idx >= end_idx:
                return None
            
            function_lines = lines[start_idx:end_idx]
            return '\n'.join(function_lines)
            
        except Exception as e:
            logger.error(f"Error extracting function by lines {start_line}-{end_line}: {e}")
            return None
    
    def _extract_diff_based_changes(self, modified_file):
        """Extract code changes based on diff analysis for non-function changes"""
        try:
            import difflib
            import re
            
            changes = []
            
            if not modified_file.source_code_before or not modified_file.source_code:
                return changes
            
            # Use simple diff approach since we can't rely on pydriller's diff_parsed
            before_lines = modified_file.source_code_before.split('\n')
            after_lines = modified_file.source_code.split('\n')
            
            # Simple diff to find changed regions
            differ = difflib.unified_diff(before_lines, after_lines, lineterm='')
            diff_lines = list(differ)
            
            if not diff_lines:
                return changes
            
            # Extract changed line numbers from unified diff
            changed_line_numbers = set()
            current_before_line = 0
            current_after_line = 0
            
            for line in diff_lines:
                if line.startswith('@@'):
                    # Parse hunk header like @@ -1,4 +1,4 @@
                    match = re.match(r'@@ -(\d+),\d+ \+(\d+),\d+ @@', line)
                    if match:
                        current_before_line = int(match.group(1))
                        current_after_line = int(match.group(2))
                elif line.startswith('-'):
                    changed_line_numbers.add(current_before_line)
                    current_before_line += 1
                elif line.startswith('+'):
                    changed_line_numbers.add(current_after_line)
                    current_after_line += 1
                elif line.startswith(' '):
                    current_before_line += 1
                    current_after_line += 1
            
            if not changed_line_numbers:
                return changes
            
            # Group consecutive changed lines and extract context
            changed_line_groups = self._group_consecutive_lines(sorted(changed_line_numbers))
            
            for group in changed_line_groups:
                start_line = max(1, min(group) - 5)  # Add context before
                end_line = min(len(before_lines), max(group) + 5)  # Add context after
                
                # Extract before and after code with context
                before_code = self._extract_function_by_lines(
                    modified_file.source_code_before, start_line, end_line
                )
                after_code = self._extract_function_by_lines(
                    modified_file.source_code, start_line, end_line
                )
                
                if before_code or after_code:
                    # Try to identify what type of code structure this is
                    structure_name = self._identify_code_structure(before_code or after_code)
                    
                    changes.append({
                        'function_name': structure_name,
                        'file_path': modified_file.filename,
                        'before_code': before_code,
                        'after_code': after_code,
                        'change_type': 'diff_based',
                        'start_line': start_line,
                        'end_line': end_line
                    })
            
            return changes
            
        except Exception as e:
            logger.error(f"Error extracting diff-based changes: {e}")
            return []
    
    def _group_consecutive_lines(self, line_numbers):
        if not line_numbers:
            return []
        
        groups = []
        current_group = [line_numbers[0]]
        
        for i in range(1, len(line_numbers)):
            if line_numbers[i] - line_numbers[i-1] <= 2:  # Allow small gaps
                current_group.append(line_numbers[i])
            else:
                groups.append(current_group)
                current_group = [line_numbers[i]]
        
        groups.append(current_group)
        return groups
    
    def _identify_code_structure(self, code_snippet):
        """Try to identify what type of code structure this is"""
        import re
        
        if not code_snippet:
            return "unknown_structure"
        
        code_lower = code_snippet.lower()
        
        if 'static const struct' in code_lower or 'struct' in code_lower:
            struct_match = re.search(r'struct\s+(\w+)', code_snippet)
            if struct_match:
                return f"struct_{struct_match.group(1)}"
            return "struct_definition"
        
        if 'static const' in code_lower and ('[]' in code_snippet or 'array' in code_lower):
            array_match = re.search(r'static\s+const\s+.*?(\w+)\s*\[', code_snippet)
            if array_match:
                return f"array_{array_match.group(1)}"
            return "static_array"
        
        if '#define' in code_snippet:
            return "macro_definition"
        
        if 'enum' in code_lower:
            return "enum_definition"
        
        if 'typedef' in code_lower:
            return "typedef_definition"
        
        if re.search(r'^\s*\w+\s+\w+\s*[=;]', code_snippet, re.MULTILINE):
            return "variable_declaration"
        
        return "code_structure"
    
    def _analyze_specification_violation(self, complete_specification, code_content, stage):
        """Analyze complete specification violation using LLM with function-level code - use spec_validate_prompts"""
        try:
            # Use spec_validate_prompts with complete specification (target + predicate)
            system_prompt = ANALYZE_VIOLATION_SYSTEM
            user_prompt = ANALYZE_VIOLATION_USER.format(
                specification=complete_specification,
                match_name=stage,
                match_code=code_content
            )

            response, input_tokens, output_tokens = self.llm_client.send_message_with_tokens(user_prompt, system_prompt)
            
            self.total_input_tokens += input_tokens
            self.total_output_tokens += output_tokens
            self.api_calls_count += 1
            
            result = self._parse_llm_result(response)
            # Add token information to the result
            result['input_tokens'] = input_tokens
            result['output_tokens'] = output_tokens
            result['total_tokens'] = input_tokens + output_tokens
            
            return result
            
        except Exception as e:
            logger.error(f"Error in LLM analysis for {stage}: {e}")
            return {
                "has_violation": False,
                "reasoning": f"Analysis error: {str(e)}",
                "confidence": "LOW",
                "raw_response": f"ERROR: {str(e)}"
            }
    
    def print_token_statistics(self):
        print("\n" + "=" * 80)
        print("📊 TOKEN USAGE STATISTICS")
        print("=" * 80)
        print(f"  Total API Calls:      {self.api_calls_count:,}")
        print(f"  Total Input Tokens:   {self.total_input_tokens:,}")
        print(f"  Total Output Tokens:  {self.total_output_tokens:,}")
        print(f"  Total Tokens:         {self.total_input_tokens + self.total_output_tokens:,}")
        print("-" * 80)
        
        if self.api_calls_count > 0:
            avg_input = self.total_input_tokens / self.api_calls_count
            avg_output = self.total_output_tokens / self.api_calls_count
            avg_total = (self.total_input_tokens + self.total_output_tokens) / self.api_calls_count
            
            print("📈 AVERAGE PER API CALL:")
            print(f"  Avg Input Tokens:     {avg_input:,.1f}")
            print(f"  Avg Output Tokens:    {avg_output:,.1f}")
            print(f"  Avg Total Tokens:     {avg_total:,.1f}")
            print("-" * 80)
        
        if self.results:
            num_specs = len(self.results)
            avg_input_per_spec = self.total_input_tokens / num_specs
            avg_output_per_spec = self.total_output_tokens / num_specs
            avg_total_per_spec = (self.total_input_tokens + self.total_output_tokens) / num_specs
            avg_calls_per_spec = self.api_calls_count / num_specs
            
            print("📋 AVERAGE PER SPECIFICATION:")
            print(f"  Specifications:       {num_specs}")
            print(f"  Avg API Calls:        {avg_calls_per_spec:.1f}")
            print(f"  Avg Input Tokens:     {avg_input_per_spec:,.1f}")
            print(f"  Avg Output Tokens:    {avg_output_per_spec:,.1f}")
            print(f"  Avg Total Tokens:     {avg_total_per_spec:,.1f}")
        
        print("=" * 80)


def parse_hexsha_list(hexsha_input):
    if not hexsha_input:
        return None
    
    # Check if it's a file path
    if os.path.exists(hexsha_input):
        print(f"📄 Reading hexsha list from file: {hexsha_input}")
        try:
            with open(hexsha_input, 'r') as f:
                hexshas = [line.strip() for line in f.readlines() if line.strip()]
            print(f"✅ Loaded {len(hexshas)} hexshas from file")
            return hexshas
        except Exception as e:
            print(f"❌ Error reading hexsha file: {e}")
            return None
    else:
        hexshas = [h.strip() for h in hexsha_input.split(',') if h.strip()]
        print(f"✅ Parsed {len(hexshas)} hexshas from command line")
        return hexshas


def test_code_changes_detection(hexsha, kernel_path):
    print(f"🧪 Testing code changes detection for hexsha: {hexsha}")
    print("=" * 80)
    
    validator = SpecValidator(kernel_path)
    
    try:
        print(f"📖 Analyzing commit: {hexsha}")
        print(f"📁 Repository path: {kernel_path}")
        
        code_changes = validator._get_function_changes_from_commit(hexsha)
        
        print(f"\n📊 Results Summary:")
        print(f"   Total code changes found: {len(code_changes)}")
        
        if code_changes:
            print(f"\n📋 Code Changes Details:")
            for i, change in enumerate(code_changes, 1):
                print(f"\n   [{i}] {change['change_type'].upper()} Change:")
                print(f"       Name: {change['function_name']}")
                print(f"       File: {change['file_path']}")
                print(f"       Lines: {change['start_line']}-{change['end_line']}")
                
                before_code = change.get('before_code')
                after_code = change.get('after_code')
                
                if before_code:
                    print(f"       Before code: {len(before_code)} characters")
                    print(f"       ┌─ BEFORE CODE ─────────────────────────────────────────")
                    for line_num, line in enumerate(before_code.split('\n'), 1):
                        print(f"       │ {line_num:3d}: {line}")
                    print(f"       └───────────────────────────────────────────────────────")
                else:
                    print(f"       Before code: None")
                
                if after_code:
                    print(f"       After code: {len(after_code)} characters")
                    print(f"       ┌─ AFTER CODE ──────────────────────────────────────────")
                    for line_num, line in enumerate(after_code.split('\n'), 1):
                        print(f"       │ {line_num:3d}: {line}")
                    print(f"       └───────────────────────────────────────────────────────")
                else:
                    print(f"       After code: None")
                
                print(f"       {'-'*60}")
        else:
            print("   ⚠️  No code changes detected")
            print("   💡 This could mean:")
            print("      - The commit doesn't modify C/H files")
            print("      - The changes are not function-level modifications") 
            print("      - The changes are in files that were added/deleted rather than modified")
            print("      - There's an issue with the commit analysis")
        
        print(f"\n✅ Debug analysis complete")
        
    except Exception as e:
        print(f"❌ Debug test failed with exception: {e}")
        import traceback
        traceback.print_exc()


def test_single_hexsha(hexsha, csv_file, kernel_path, llm_config_path=None, target_col="target", predicate_col="predicate"):
    print(f"🧪 Testing single specification validation: {hexsha}")
    print("=" * 80)
    
    validator = SpecValidator(
        kernel_path,
        target_col=target_col,
        predicate_col=predicate_col,
        llm_config_path=llm_config_path,
    )
    validator.print_config()
    
    try:
        print(f"\n📖 Reading CSV file: {csv_file}")
        df = pd.read_csv(csv_file)
        target_row = df[df['hexsha'] == hexsha]
        
        if target_row.empty:
            print(f"❌ Cannot find hexsha '{hexsha}' in CSV file")
            return
        
        print(f"✅ Found specification for {hexsha}")
        row = target_row.iloc[0]
        result = validator.validate_single_specification(row)
        
        print(f"\n{'='*80}")
        print(f"Complete Specification Validation Result Details")
        print(f"{'='*80}")
        print(f"Hexsha: {result.get('hexsha', 'N/A')}")
        print(f"Target: {result.get('target', 'N/A')}")
        print(f"Predicate: {result.get('predicate', 'N/A')}")
        print(f"\nOverall validation result: {result.get('validation_status', 'N/A')}")
        print(f"Specification validation: {result.get('specification_valid', 'N/A')}")
        print(f"Function changes analyzed: {result.get('function_changes_count', 'N/A')}")
        print(f"Valid changes: {result.get('valid_changes_count', 'N/A')}")
        print(f"\n📊 Token Usage:")
        print(f"  Input tokens:  {result.get('input_tokens', 0):,}")
        print(f"  Output tokens: {result.get('output_tokens', 0):,}")
        print(f"  Total tokens:  {result.get('total_tokens', 0):,}")
        print(f"  API calls:     {result.get('api_calls', 0)}")
        print(f"\nSummary reasoning: {result.get('summary_reasoning', 'N/A')}")
        print(f"\nBefore patch reasoning: {result.get('before_patch_reasoning', 'N/A')[:200]}{'...' if len(result.get('before_patch_reasoning', '')) > 200 else ''}")
        print(f"\nAfter patch reasoning: {result.get('after_patch_reasoning', 'N/A')[:200]}{'...' if len(result.get('after_patch_reasoning', '')) > 200 else ''}")
        
        function_details = result.get('function_analysis_details', [])
        if function_details:
            print(f"\n{'='*80}")
            print(f"Detailed Function-Level Analysis ({len(function_details)} functions)")
            print(f"{'='*80}")
            
            for i, func_detail in enumerate(function_details, 1):
                print(f"\n[{i}] Function: {func_detail.get('function_name', 'N/A')}")
                print(f"    File: {func_detail.get('file_path', 'N/A')}")
                print(f"    Validation Result: {func_detail.get('validation_result', 'N/A')}")
                
                pre_analysis = func_detail.get('pre_patch_analysis')
                if pre_analysis and func_detail.get('has_pre_patch_code'):
                    print(f"\n    📋 Pre-patch Analysis:")
                    print(f"       Has Violation: {pre_analysis.get('has_violation', 'N/A')}")
                    print(f"       Confidence: {pre_analysis.get('confidence', 'N/A')}")
                    reasoning = pre_analysis.get('reasoning', 'N/A')
                    if len(reasoning) > 200:
                        print(f"       Reasoning: {reasoning[:200]}...")
                    else:
                        print(f"       Reasoning: {reasoning}")
                else:
                    print(f"    📋 Pre-patch Analysis: No code available")
                
                post_analysis = func_detail.get('post_patch_analysis')
                if post_analysis and func_detail.get('has_post_patch_code'):
                    print(f"\n    📋 Post-patch Analysis:")
                    print(f"       Has Violation: {post_analysis.get('has_violation', 'N/A')}")
                    print(f"       Confidence: {post_analysis.get('confidence', 'N/A')}")
                    reasoning = post_analysis.get('reasoning', 'N/A')
                    if len(reasoning) > 200:
                        print(f"       Reasoning: {reasoning[:200]}...")
                    else:
                        print(f"       Reasoning: {reasoning}")
                else:
                    print(f"    📋 Post-patch Analysis: No code available")
                
                print(f"    {'-'*60}")
        else:
            print(f"\nNo detailed function analysis available")
        
        validator.results.append(result)
        output_file = f"test_spec_validation_{hexsha[:8]}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        validator.save_results(output_file)
        print(f"\nResults saved to: {output_file}")
        
        # Print token statistics for test mode
        validator.print_token_statistics()
        
    except Exception as e:
        logger.error(f"Test failed: {e}")
        import traceback
        traceback.print_exc()


def main():
    parser = argparse.ArgumentParser(description='Complete specification validator (target + predicate)')
    parser.add_argument('--mode', choices=['test', 'batch', 'debug'], default='batch',
                       help='Run mode: test(test single hexsha), batch(batch validation), debug(debug code changes detection)')
    parser.add_argument('--hexsha', help='Hexsha to test (test mode only)')
    parser.add_argument('--input-file', '-i',
                       help='Input CSV file path (output from spec_extract.py)')
    parser.add_argument('--output-file', '-o',
                       help='Output file name')
    parser.add_argument('--kernel-path', '-k',
                       default='./linux',
                       help='Linux kernel source path')
    parser.add_argument('--target-col', default='target', help='Column name for target (default: target)')
    parser.add_argument('--predicate-col', default='predicate', help='Column name for predicate (default: predicate)')
    parser.add_argument('--llm-config', help='Path to JSON config overriding LLM settings')
    parser.add_argument('--hexsha-list', help='Path to file containing list of hexshas to process (one per line), or comma-separated hexshas')

    args = parser.parse_args()

    if args.mode == 'debug':
        if not args.hexsha:
            print("Error: debug mode requires --hexsha parameter")
            print("Usage: python spec_validator.py --mode debug --hexsha <hexsha>")
            sys.exit(1)
        # Debug mode: test code changes detection only
        test_code_changes_detection(args.hexsha, args.kernel_path)
        return

    if args.mode == 'test':
        if not args.hexsha or not args.input_file:
            print("Error: test mode requires --hexsha and --input-file parameters")
            print("Usage: python spec_validator.py --mode test --hexsha <hexsha> --input-file <csv_file>")
            sys.exit(1)
        validator = SpecValidator(
            args.kernel_path,
            target_col=args.target_col,
            predicate_col=args.predicate_col,
            llm_config_path=args.llm_config,
        )
        validator.print_config()
        import pandas as pd
        df = pd.read_csv(args.input_file)
        target_row = df[df['hexsha'] == args.hexsha]
        if target_row.empty:
            print(f"❌ Cannot find hexsha '{args.hexsha}' in CSV file")
            sys.exit(1)
        row = target_row.iloc[0]
        result = validator.validate_single_specification(row)
        validator.results.append(result)
        output_file = f"test_spec_validation_{args.hexsha[:8]}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        validator.save_results(output_file)
        print(f"\nResults saved to: {output_file}")
        validator.print_token_statistics()
        return

    if not args.input_file:
        print("Error: batch mode requires --input-file parameter")
        print("Usage: python spec_validator.py --input-file <csv_file>")
        sys.exit(1)

    if not os.path.exists(args.input_file):
        print(f"Error: input file does not exist: {args.input_file}")
        sys.exit(1)

    logger.info("Starting complete specification validation")

    hexsha_filter = None
    if args.hexsha_list:
        hexsha_filter = parse_hexsha_list(args.hexsha_list)
        if not hexsha_filter:
            print("❌ Failed to parse hexsha list")
            sys.exit(1)

    validator = SpecValidator(
        args.kernel_path,
        target_col=args.target_col,
        predicate_col=args.predicate_col,
        llm_config_path=args.llm_config,
    )
    validator.print_config()
    validator.validate_csv(args.input_file, args.output_file, hexsha_filter)


if __name__ == "__main__":
    main()
