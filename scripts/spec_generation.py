#!/usr/bin/env python3
"""
Specification Generation Tool
Analyzes similar targets and generates concrete specifications using LLM.
"""
import argparse
import pandas as pd
import json
import sys
import os
from pathlib import Path
from datetime import datetime
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Dict, Any, List

try:
    from scripts.utils.artifact_utils import configure_script_imports, filter_preserve_order
except ImportError:
    from utils.artifact_utils import configure_script_imports, filter_preserve_order

configure_script_imports(__file__)

# Import prompt loader and shared utilities
from prompt_loader import PromptLoader
from shared_utils import parse_llm_json_response
from openai_client import OpenAIClient

# Import CodeSearcher for function code and usage extraction
try:
    from CodeSearcher import CodeSearcher
except ImportError:
    print("⚠️  Warning: CodeSearcher module not found")
    CodeSearcher = None


@dataclass
class TokenStats:
    input_tokens: int = 0
    output_tokens: int = 0
    total_requests: int = 0
    
    def add_request(self, input_tokens: int, output_tokens: int):
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        self.total_requests += 1
    
    def __str__(self):
        return f"Requests: {self.total_requests}, Input tokens: {self.input_tokens:,}, Output tokens: {self.output_tokens:,}, Total: {self.input_tokens + self.output_tokens:,}"


def filter_similar_targets_for_review(similar_target_list, allowlist):
    return filter_preserve_order(similar_target_list, allowlist)


class SpecificationGenerator:
    def __init__(
        self,
        source_dir="./linux",
        max_workers=20,
        max_usage_examples=5,
        model="claude-sonnet-4-20250514",
        target_allowlist=None,
    ):
        self.results = []
        self.source_dir = source_dir
        self.max_workers = max_workers
        self.max_usage_examples = max_usage_examples
        self.model = model
        self.target_allowlist = target_allowlist or []
        
        self.results_lock = threading.Lock()
        self.save_lock = threading.Lock()
        
        self.token_stats = TokenStats()
        self.token_stats_lock = threading.Lock()
        
        # Initialize prompt loader and LLM client
        self.prompt_loader = PromptLoader()
        self.llm_client = OpenAIClient(model=self.model)
        
        # Initialize CodeSearcher for source code analysis
        self.code_searcher = None
        if CodeSearcher:
            try:
                # Check if tree_sitter is available (CodeSearcher dependency)
                import tree_sitter
                self.code_searcher = CodeSearcher(self.source_dir)
                print("✅ CodeSearcher initialized")
            except ImportError as e:
                print(f"⚠️  CodeSearcher dependencies missing: {e}")
                print("   Source code and usage examples will use placeholder data")
            except Exception as e:
                print(f"⚠️  Failed to initialize CodeSearcher: {e}")
                print("   Source code and usage examples will use placeholder data")
        else:
            print("⚠️  CodeSearcher not available")
        
        print(f"✅ LLM client initialized")
        print("✅ Prompt loader initialized")
    
    def _parse_json_response(self, response: str) -> dict:
        try:
            return parse_llm_json_response(response)
        except (json.JSONDecodeError, ValueError) as e:
            print(f"  ⚠️  JSON parse error: {e}")
            # Ensure response is a string before slicing
            response_str = str(response) if not isinstance(response, str) else response
            print(f"  Raw response: {response_str[:200]}...")
            return {}

    def _get_function_source_code(self, function_name: str) -> str:
        if self.code_searcher:
            try:
                results = self.code_searcher.query_given_func_code(function_name)
                if results:
                    # Return the first matching result as source code
                    return results[0] if isinstance(results, list) else str(results)
                else:
                    return f"No source code found for function: {function_name}"
            except Exception as e:
                return f"Error retrieving source code for {function_name}: {e}"
        else:
            return f"CodeSearcher not available. Cannot retrieve source for: {function_name}"

    def _get_function_usage_examples(self, function_name: str) -> str:
        if self.code_searcher:
            try:
                results = self.code_searcher.query_given_func_usage(function_name)
                if results:
                    if isinstance(results, list):
                        usage_examples = "\n\n".join([f"Usage Example {i+1}:\n{result}" for i, result in enumerate(results[:self.max_usage_examples])])  # Limit to configured number of examples
                    else:
                        usage_examples = f"Usage Example:\n{results}"
                    return usage_examples
                else:
                    return f"No usage examples found for function: {function_name}"
            except Exception as e:
                return f"Error retrieving usage examples for {function_name}: {e}"
        else:
            return f"CodeSearcher not available. Cannot retrieve usage examples for: {function_name}"

    def analyze_function(self, generalized_spec, target_func, description, similarity_score, spec_example=""):
        """
        Analyze a single function against the generalized specification
        """
        # Note: Print statements moved to _process_target_function for thread safety
        
        try:
            source_code = self._get_function_source_code(target_func)
            usage_examples = self._get_function_usage_examples(target_func)
            
            system_prompt = self.prompt_loader.get_step4_prompt("specification_generation_system")
            user_prompt = self.prompt_loader.get_step4_prompt(
                "specification_generation_user",
                generalized_spec=generalized_spec,
                target=target_func,
                description=description,
                similarity_score=similarity_score,
                source_code=source_code,
                usage_examples=usage_examples,
                spec_example=spec_example
            )
            
            # Use send_message_with_tokens to get actual token counts
            response, input_tokens, output_tokens = self.llm_client.send_message_with_tokens(user_prompt, system_prompt)
            
            with self.token_stats_lock:
                self.token_stats.add_request(input_tokens, output_tokens)
            
            # Parse JSON response (similar to spec_generalize.py)
            result = self._parse_json_response(response)
            
            if result:
                # print(f"  ✅ Analysis complete: {result.get('judgement', 'unknown')}")  # Moved to thread wrapper
                # Detailed prints moved to thread wrapper for cleaner output
                return result
            else:
                # print(f"  ❌ Failed to parse LLM response")  # Moved to thread wrapper
                return {
                    "judgement": "no",
                    "reason": "Failed to parse LLM response",
                    "evidence": [],
                    "concretized_specification": None
                }
                
        except Exception as e:
            # print(f"  ❌ Analysis failed: {e}")  # Moved to thread wrapper
            return {
                "judgement": "no",
                "reason": f"Analysis error: {str(e)}",
                "evidence": [],
                "concretized_specification": None
            }

    def _process_target_function(self, args_tuple):
        """Process a single target function - thread-safe wrapper for analyze_function"""
        generalized_spec, target_func, description, similarity_score, spec_example, thread_id = args_tuple
        
        try:
            print(f"[Thread-{thread_id}] 🔍 Analyzing: {target_func} (sim: {similarity_score:.3f})")
            
            result = self.analyze_function(generalized_spec, target_func, description, similarity_score, spec_example)
            
            judgement = result.get('judgement', 'unknown')
            if judgement == 'yes':
                spec_text = result.get('concretized_specification', '')
                if isinstance(spec_text, str) and len(spec_text) > 50:
                    spec_preview = spec_text[:50] + "..."
                else:
                    spec_preview = str(spec_text)[:50]
                print(f"[Thread-{thread_id}] ✅ {target_func} -> QUALIFIED: {spec_preview}")
            else:
                reason = result.get('reason', 'Unknown reason')
                print(f"[Thread-{thread_id}] ❌ {target_func} -> REJECTED: {reason[:60]}...")
            
            return target_func, result
            
        except Exception as e:
            print(f"[Thread-{thread_id}] 💥 ERROR {target_func}: {e}")
            return target_func, {
                "judgement": "no",
                "reason": f"Thread processing error: {str(e)}",
                "evidence": [],
                "concretized_specification": None
            }

    def process_single_row(self, row_data: dict) -> dict:
        """Process a single row from the similar target search results"""
        hexsha = row_data['hexsha']
        generalized_target = row_data.get('generalized_target', '')
        generalized_predicate = row_data.get('generalized_predicate', '')
        
        # Create spec_example from CSV target and predicate as JSON object
        original_target = row_data.get('target', '')
        original_predicate = row_data.get('predicate', '')
        spec_example = json.dumps({
            "target": original_target,
            "predicate": original_predicate
        }, ensure_ascii=False, indent=2)
        
        # Combine target and predicate for generalized spec
        generalized_spec = f"Target: {generalized_target}\\nPredicate: {generalized_predicate}"
        
        print(f"Processing {hexsha[:12]}...")
        
        result = row_data.copy()
        
        try:
            similar_target_list = json.loads(row_data.get('similar_target_list', '[]'))
            target_descriptions = json.loads(row_data.get('target_descriptions', '{}'))
            similarity_scores = json.loads(row_data.get('similarity_scores', '{}'))
        except json.JSONDecodeError:
            print(f"  ⚠️  Skipping {hexsha[:12]} - invalid similar targets data")
            result.update({
                "qualified_targets": "[]",
                "generated_specifications": "{}",
                "specification_count": 0
            })
            return result
        
        if not similar_target_list:
            print(f"  ⚠️  Skipping {hexsha[:12]} - no similar targets found")
            result.update({
                "qualified_targets": "[]",
                "generated_specifications": "{}",
                "specification_count": 0
            })
            return result

        review_targets = filter_similar_targets_for_review(similar_target_list, self.target_allowlist)
        if self.target_allowlist:
            print(f"  🎯 Reviewer target filter active: {len(review_targets)}/{len(similar_target_list)} targets kept")
        if not review_targets:
            result.update({
                "qualified_targets": "[]",
                "generated_specifications": "{}",
                "all_analysis_results": "{}",
                "specification_count": 0
            })
            return result
        
        # Analyze each similar target using multi-threading
        qualified_targets = []
        generated_specifications = {}
        all_analysis_results = {}  # Store all analysis results including failures
        
        if len(review_targets) <= 1:
            # Single target or no targets - use sequential processing
            for target_func in review_targets:
                description = target_descriptions.get(target_func, "No description available")
                similarity_score = similarity_scores.get(target_func, 0.0)
                
                analysis_result = self.analyze_function(
                    generalized_spec, target_func, description, similarity_score, spec_example
                )
                
                # Always store the complete analysis result
                all_analysis_results[target_func] = {
                    "judgement": analysis_result.get('judgement', 'no'),
                    "reason": analysis_result.get('reason', 'Unknown reason'),
                    "evidence": analysis_result.get('evidence', []),
                    "similarity_score": similarity_score,
                    "description": description
                }
                
                # Only add to qualified targets and generated specifications if accepted
                if analysis_result.get('judgement') == 'yes':
                    qualified_targets.append(target_func)
                    generated_specifications[target_func] = {
                        "specification": analysis_result.get('concretized_specification'),
                        "reason": analysis_result.get('reason'),
                        "evidence": analysis_result.get('evidence', []),
                        "similarity_score": similarity_score
                    }
        else:
            print(f"  🧵 Using multi-threading with {min(self.max_workers, len(review_targets))} workers")
            
            thread_args = []
            for i, target_func in enumerate(review_targets):
                description = target_descriptions.get(target_func, "No description available")
                similarity_score = similarity_scores.get(target_func, 0.0)
                thread_args.append((generalized_spec, target_func, description, similarity_score, spec_example, i+1))
            
            with ThreadPoolExecutor(max_workers=min(self.max_workers, len(review_targets))) as executor:
                future_to_target = {
                    executor.submit(self._process_target_function, args): args[1] 
                    for args in thread_args
                }
                
                for future in as_completed(future_to_target):
                    target_func, analysis_result = future.result()
                    
                    # Always store the complete analysis result
                    description = target_descriptions.get(target_func, "No description available")
                    similarity_score = similarity_scores.get(target_func, 0.0)
                    
                    all_analysis_results[target_func] = {
                        "judgement": analysis_result.get('judgement', 'no'),
                        "reason": analysis_result.get('reason', 'Unknown reason'),
                        "evidence": analysis_result.get('evidence', []),
                        "similarity_score": similarity_score,
                        "description": description
                    }
                    
                    # Only add to qualified targets and generated specifications if accepted
                    if analysis_result.get('judgement') == 'yes':
                        qualified_targets.append(target_func)
                        generated_specifications[target_func] = {
                            "specification": analysis_result.get('concretized_specification'),
                            "reason": analysis_result.get('reason'),
                            "evidence": analysis_result.get('evidence', []),
                            "similarity_score": similarity_score
                        }
        
        result.update({
            "qualified_targets": json.dumps(qualified_targets),
            "generated_specifications": json.dumps(generated_specifications),
            "all_analysis_results": json.dumps(all_analysis_results),  # NEW: Store all results
            "specification_count": len(qualified_targets)
        })
        
        print(f"  ✅ Generated {len(qualified_targets)} specifications out of {len(similar_target_list)} targets")
        
        return result

    def _generate_output_filename(self, input_csv):
        # Get model name and clean it for filename (remove special characters)
        model_name = self.model.replace('/', '_').replace('-', '_').replace('.', '_')
        
        # Extract timestamp and parameters from input filename
        match = re.search(r'step3_similar_target_search_(\d{8}_\d{6}_threshold_\d+_\d+_topk_\d+)\.csv$', input_csv)
        
        if match:
            timestamp_and_params = match.group(1)
            return f"step4_specification_generation_{timestamp_and_params}_{model_name}.csv"
        else:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            return f"step4_specification_generation_{timestamp}_{model_name}.csv"

    def process_csv(self, input_csv, output_csv=None, retry_failed=None):
        """Process all rows from the similar target search CSV file
        
        Args:
            input_csv: Input CSV file path
            output_csv: Output CSV file path (optional)
            retry_failed: Whether to retry failed items (True/False/None for interactive)
        """
        print(f"Reading similar target search results from: {input_csv}")
        
        if output_csv is None:
            final_csv = self._generate_output_filename(input_csv)
        else:
            final_csv = output_csv
        
        existing_results = {}
        if os.path.exists(final_csv):
            print(f"📁 Found existing results file: {final_csv}")
            try:
                existing_df = pd.read_csv(final_csv)
                existing_results = {row['hexsha']: row for _, row in existing_df.iterrows()}
                print(f"📋 Loaded {len(existing_results)} existing results")
            except Exception as e:
                print(f"⚠️  Warning: Could not load existing results: {e}")
        
        df = pd.read_csv(input_csv, comment='#')
        rows = df.to_dict('records')
        
        # Filter out already processed rows (excluding failed ones that might be retried)
        pending_rows = []
        failed_rows = []
        
        for row in rows:
            hexsha = row['hexsha']
            if hexsha in existing_results:
                existing_result = existing_results[hexsha]
                # Check if this is a failed result
                if isinstance(existing_result, dict) and existing_result.get('processing_status') == 'failed':
                    failed_rows.append(row)
                # If not failed, it's already successfully processed
            else:
                pending_rows.append(row)
        
        print(f"📊 Total rows: {len(rows)}")
        print(f"   • Already successfully processed: {len(existing_results) - len(failed_rows)}")
        print(f"   • Previously failed: {len(failed_rows)}")
        print(f"   • Never processed: {len(pending_rows)}")
        
        # Ask user if they want to retry failed items (unless specified via command line)
        if failed_rows:
            if retry_failed is True:
                pending_rows.extend(failed_rows)
                print(f"✅ Auto-retrying {len(failed_rows)} failed items (--retry-failed)")
            elif retry_failed is False:
                print(f"⏭️  Auto-skipping {len(failed_rows)} failed items (--skip-failed)")
            else:
                try:
                    retry_choice = input(f"\\n❓ Found {len(failed_rows)} previously failed items. Retry them? (y/n): ").strip().lower()
                    if retry_choice in ['y', 'yes']:
                        pending_rows.extend(failed_rows)
                        print(f"✅ Will retry {len(failed_rows)} failed items")
                    else:
                        print(f"⏭️  Skipping {len(failed_rows)} failed items")
                except KeyboardInterrupt:
                    print(f"\\n⏭️  Skipping failed items retry")
        
        print(f"📝 Total pending for processing: {len(pending_rows)}")
        
        for hexsha, result in existing_results.items():
            if not (isinstance(result, dict) and result.get('processing_status') == 'failed'):
                self.results.append(result.to_dict() if hasattr(result, 'to_dict') else result)
        
        if not pending_rows:
            print("✅ All rows already processed!")
            return
        
        # Process pending rows with individual error handling and immediate saving
        processed_count = 0
        failed_count = 0
        
        try:
            for i, row in enumerate(pending_rows, 1):
                hexsha = row['hexsha']
                print(f"[{i}/{len(pending_rows)}] Processing {hexsha}")
                
                try:
                    # Process single row with error handling
                    result = self.process_single_row(row)
                    self.results.append(result)
                    processed_count += 1
                    
                    # Save progress immediately after each successful processing
                    self._save_progress(final_csv)
                    print(f"  ✅ Completed and saved ({processed_count} successful, {failed_count} failed)")
                    
                except Exception as e:
                    failed_count += 1
                    print(f"  ❌ Failed to process {hexsha}: {e}")
                    
                    # Create a failed result entry to track the failure
                    failed_result = row.copy()
                    failed_result.update({
                        "qualified_targets": "[]",
                        "generated_specifications": "{}",
                        "specification_count": 0,
                        "processing_status": "failed",
                        "error_message": str(e),
                        "processed_at": datetime.now().isoformat()
                    })
                    self.results.append(failed_result)
                    
                    # Save progress even for failed items
                    self._save_progress(final_csv)
                    print(f"  💾 Failed result logged and saved ({processed_count} successful, {failed_count} failed)")
                    
                    continue
                
                # Optional: Add a small delay to avoid overwhelming the LLM API
                import time
                time.sleep(1)
        
        except KeyboardInterrupt:
            print(f"\\n⚠️  Process interrupted by user")
            self._save_progress(final_csv)
            print(f"💾 Progress saved to: {final_csv}")
            print(f"📊 Status: {processed_count} successful, {failed_count} failed")
            if self.token_stats.total_requests > 0:
                print(f"🎯 Token Usage: {self.token_stats}")
            print("🔄 You can resume by running the same command again")
            return
        except Exception as e:
            print(f"\\n❌ Unexpected error occurred: {e}")
            self._save_progress(final_csv)
            print(f"💾 Progress saved to: {final_csv}")
            print(f"📊 Status: {processed_count} successful, {failed_count} failed")
            raise
        
        self._save_progress(final_csv)
        
        total = len(self.results)
        successful_with_specs = sum(1 for r in self.results if r.get('specification_count', 0) > 0)
        successful_processed = sum(1 for r in self.results if r.get('processing_status') != 'failed')
        failed_processed = sum(1 for r in self.results if r.get('processing_status') == 'failed')
        total_specs = sum(r.get('specification_count', 0) for r in self.results)
        
        print(f"\\n✅ Specification generation complete!")
        print(f"📊 Processing Summary:")
        print(f"   • Total commits: {total}")
        print(f"   • Successfully processed: {successful_processed}")
        print(f"   • Failed to process: {failed_processed}")
        print(f"   • Commits with generated specifications: {successful_with_specs}")
        print(f"   • Total specifications generated: {total_specs}")
        print(f"💾 Results saved to: {final_csv}")
        
        print(f"\\n🎯 Token Usage Statistics:")
        print(f"   {self.token_stats}")
        if self.token_stats.total_requests > 0:
            avg_input = self.token_stats.input_tokens / self.token_stats.total_requests
            avg_output = self.token_stats.output_tokens / self.token_stats.total_requests
            print(f"   Average per request - Input: {avg_input:.0f}, Output: {avg_output:.0f}")
        
        json_file = final_csv.replace('.csv', '.json')
        print(f"💾 JSON results saved to: {json_file}")
        
        if failed_processed > 0:
            print(f"\\n⚠️  {failed_processed} commits failed to process. Check the CSV file for error details.")
            print("🔄 You can re-run the script to retry failed items (they will be skipped if marked as failed).")
    
    def _save_progress(self, output_file):
        """Save current progress to file with backup and atomic write"""
        try:
            # Create backup of existing file if it exists
            if os.path.exists(output_file):
                backup_file = f"{output_file}.backup"
                import shutil
                shutil.copy2(output_file, backup_file)
            
            # Use temporary file for atomic write
            temp_file = f"{output_file}.tmp"
            
            results_df = pd.DataFrame(self.results)
            results_df.to_csv(temp_file, index=False)
            
            import shutil
            shutil.move(temp_file, output_file)
            
            json_file = output_file.replace('.csv', '.json')
            self._save_json_results(json_file)
            
        except Exception as e:
            print(f"⚠️  Failed to save progress: {e}")
            # Clean up temp file if it exists
            temp_file = f"{output_file}.tmp"
            if os.path.exists(temp_file):
                try:
                    os.remove(temp_file)
                except:
                    pass
    
    def _save_json_results(self, json_file):
        """Save results in JSON format with proper structure and atomic write"""
        try:
            json_results = []
            for result in self.results:
                json_result = result.copy()
                
                # Parse JSON strings back to objects for cleaner JSON output
                for field in ['qualified_targets', 'generated_specifications', 'all_analysis_results', 'similar_target_list', 'target_descriptions', 'similarity_scores']:
                    if field in json_result:
                        try:
                            json_result[field] = json.loads(json_result[field])
                        except (json.JSONDecodeError, TypeError):
                            pass
                
                json_results.append(json_result)
            
            # Use temporary file for atomic write
            temp_file = f"{json_file}.tmp"
            
            with open(temp_file, 'w', encoding='utf-8') as f:
                json.dump(json_results, f, indent=2, ensure_ascii=False)
            
            import shutil
            shutil.move(temp_file, json_file)
            
        except Exception as e:
            print(f"⚠️  Failed to save JSON results: {e}")
            # Clean up temp file if it exists
            temp_file = f"{json_file}.tmp"
            if os.path.exists(temp_file):
                try:
                    os.remove(temp_file)
                except:
                    pass

    def test_single_commit(self, input_csv, hexsha):
        print(f"🧪 Testing specification generation for commit: {hexsha}")
        
        # Read input CSV and find the specified commit
        df = pd.read_csv(input_csv, comment='#')
        target_row = df[df['hexsha'] == hexsha]
        
        if target_row.empty:
            print(f"❌ Commit {hexsha} not found in {input_csv}")
            return
        
        row_data = target_row.iloc[0].to_dict()
        
        print(f"📋 Found commit: {hexsha}")
        print(f"  🎯 Generalized target: {row_data.get('generalized_target', 'N/A')}")
        print(f"  📝 Predicate: {row_data.get('predicate', 'N/A')}")
        
        result = self.process_single_row(row_data)
        
        print(f"\\n" + "="*60)
        print(f"🎯 Test Results for {hexsha[:12]}")
        print("="*60)
        
        try:
            qualified_targets = json.loads(result['qualified_targets'])
            generated_specs = json.loads(result['generated_specifications'])
            all_analysis = json.loads(result.get('all_analysis_results', '{}'))
            
            print(f"📊 Analysis Summary:")
            print(f"   • Total targets analyzed: {len(all_analysis)}")
            print(f"   • Qualified targets: {len(qualified_targets)}")
            print(f"   • Rejected targets: {len(all_analysis) - len(qualified_targets)}")
            
            if qualified_targets:
                print(f"\\n✅ Qualified targets and their specifications:")
                for i, target in enumerate(qualified_targets, 1):
                    spec_data = generated_specs.get(target, {})
                    spec = spec_data.get('specification', 'No specification')
                    score = spec_data.get('similarity_score', 0.0)
                    print(f"\\n{i}. {target} (similarity: {score:.3f})")
                    print(f"   📝 Specification: {spec}")
                    if spec_data.get('evidence'):
                        print(f"   🔍 Evidence: {spec_data['evidence'][:2]}")  # Show first 2 evidence items
            
            rejected_targets = [target for target in all_analysis.keys() if target not in qualified_targets]
            if rejected_targets:
                print(f"\\n❌ Rejected targets and reasons:")
                for i, target in enumerate(rejected_targets[:5], 1):  # Show first 5 rejected
                    analysis_data = all_analysis.get(target, {})
                    reason = analysis_data.get('reason', 'Unknown reason')
                    score = analysis_data.get('similarity_score', 0.0)
                    print(f"\\n{i}. {target} (similarity: {score:.3f})")
                    print(f"   ❌ Reason: {reason}")
                    if analysis_data.get('evidence'):
                        print(f"   🔍 Evidence: {analysis_data['evidence'][:2]}")
                
                if len(rejected_targets) > 5:
                    print(f"\\n   ... and {len(rejected_targets) - 5} more rejected targets")
            
            if not qualified_targets and not rejected_targets:
                print("❌ No targets qualified for specification generation")
                
        except json.JSONDecodeError:
            print("❌ Error parsing results")


def main():
    parser = argparse.ArgumentParser(description='Specification Generation Tool')
    parser.add_argument('input_csv', nargs='?', help='Input CSV file with similar target search results')
    parser.add_argument('--output', help='Output CSV file')
    parser.add_argument('--test', help='Test single commit by hexsha')
    parser.add_argument('--source-dir', help='Linux kernel source directory for CodeSearcher')
    parser.add_argument('--retry-failed', action='store_true', 
                       help='Automatically retry previously failed items without prompting')
    parser.add_argument('--skip-failed', action='store_true',
                       help='Automatically skip previously failed items without prompting')
    parser.add_argument('--test-filename', action='store_true',
                       help='Test filename generation with model info')
    parser.add_argument('--max-workers', type=int, default=30,
                       help='Maximum number of concurrent threads (default: 4)')
    parser.add_argument('--max-usage-examples', type=int, default=5,
                       help='Maximum number of usage examples to include (default: 5)')
    parser.add_argument('--model', type=str, default="claude-sonnet-4-20250514",
                       help='LLM model to use')
    
    args = parser.parse_args()
    
    # Initialize generator with custom parameters if provided
    source_dir = args.source_dir if args.source_dir else "/root/linux"
    generator = SpecificationGenerator(
        source_dir=source_dir, 
        max_workers=args.max_workers,
        max_usage_examples=args.max_usage_examples,
        model=args.model,
    )
    
    if args.test_filename:
        print("🧪 Testing filename generation with model info:")
        test_files = [
            "step3_similar_target_search_20251015_172111_threshold_0_35_topk_100.csv",
            "step3_similar_target_search_20240101_120000_threshold_0_50_topk_200.csv"
        ]
        for test_file in test_files:
            output_name = generator._generate_output_filename(test_file)
            json_name = output_name.replace('.csv', '.json')
            print(f"  📄 Input: {test_file}")
            print(f"  📄 CSV Output: {output_name}")
            print(f"  📄 JSON Output: {json_name}")
            print()
        return
    
    if args.test:
        if not args.input_csv:
            parser.error("input_csv is required when using --test mode")
        generator.test_single_commit(args.input_csv, args.test)
        return
    
    if not args.input_csv:
        parser.error("input_csv is required when not using --test mode")
    
    retry_failed = None
    if args.retry_failed:
        retry_failed = True
    elif args.skip_failed:
        retry_failed = False
    
    generator.process_csv(args.input_csv, args.output, retry_failed)


if __name__ == '__main__':
    main()
