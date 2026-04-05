#!/usr/bin/env python3
"""
Step 3: Bug Detection (Multi-threaded Version)
This script performs vulnerability detection based on specifications from Step 2.
Uses multi-threading to accelerate LLM requests for better performance.
"""

import argparse
import pandas as pd
import json
import sys
import os
from pathlib import Path
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
import time

# Configure repo-relative imports for the flattened submission layout.
try:
    from scripts.utils.artifact_utils import configure_script_imports, filter_preserve_order
except ImportError:
    from utils.artifact_utils import configure_script_imports, filter_preserve_order

configure_script_imports(__file__)

# Import utils directly - no dependency on simplified_pipeline
from prompt_loader import PromptLoader
from shared_utils import ResultFormatter, StatusReporter, CSVProcessor
from CodeSearcher import CodeSearcher
from openai_client import OpenAIClient


def filter_candidate_matches_for_review(matching_functions, allowlist):
    if not allowlist:
        return matching_functions

    filtered_names = filter_preserve_order(matching_functions.keys(), allowlist)
    return {name: matching_functions[name] for name in filtered_names}



class ThreadedBugDetector:
    def __init__(
        self,
        kernel_path,
        model="claude-sonnet-4-20250514",
        max_matches_to_analyze=20,
        max_workers=4,
        spec_target_allowlist=None,
        candidate_function_allowlist=None,
    ):
        self.kernel_path = kernel_path
        self.model = model
        self.max_matches_to_analyze = max_matches_to_analyze
        self.max_workers = max_workers
        self.spec_target_allowlist = spec_target_allowlist or []
        self.candidate_function_allowlist = candidate_function_allowlist or []
        self.results = []
        self.json_results = []  # New JSON format results
        
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.total_tokens = 0
        
        self.results_lock = Lock()
        self.print_lock = Lock()
        self.token_lock = Lock()  # Lock for token counter updates
        
        self.prompt_loader = PromptLoader()
        self.code_searcher = CodeSearcher(kernel_path)
        
        # Create multiple LLM clients for threading
        self.llm_clients = []
        for i in range(max_workers):
            client = OpenAIClient(
                model=self.model,
                system_prompt="You are a weggli query expert for Linux kernel code analysis."
            )
            self.llm_clients.append(client)
    
    def get_llm_client(self, thread_id=0):
        return self.llm_clients[thread_id % len(self.llm_clients)]
    
    def thread_safe_print(self, message):
        with self.print_lock:
            print(message)
    
    def update_token_usage(self, input_tokens, output_tokens):
        with self.token_lock:
            self.total_input_tokens += input_tokens
            self.total_output_tokens += output_tokens
            self.total_tokens += (input_tokens + output_tokens)
    
    def generate_weggli_query(self, func_name: str, target_description: str) -> str:
        """Generate weggli query for the given function and target description"""
        system_prompt = self.prompt_loader.get_step3_prompt("generate_weggli_system")
        user_prompt = self.prompt_loader.get_step3_prompt(
            "generate_weggli_user",
            func_name=func_name,
            target_description=target_description
        )
        
        # Use first client for query generation (single-threaded)
        response, input_tokens, output_tokens = self.llm_clients[0].send_message_with_tokens(user_prompt, system_prompt)
        self.update_token_usage(input_tokens, output_tokens)
        
        query = response.strip()
        
        if "```" in query:
            query = query.split("```")[1].strip()
        
        if query.startswith('"') and query.endswith('"'):
            query = query[1:-1]
        elif query.startswith("'") and query.endswith("'"):
            query = query[1:-1]
        
        # Ensure query is quoted for weggli execution
        if not (query.startswith("'") and query.endswith("'")):
            query = f"'{query}'"
            
        print(f"    📝 Generated weggli query: {query}")
        return query

    def localize_candidates_for_spec_with_metadata(self, func_name: str, target_spec: str) -> dict:
        generated_query = self.generate_weggli_query(func_name, target_spec)
        normalized_query = str(generated_query or "").strip().strip("'").strip('"').strip()

        matching_functions = {}
        localization_error = ""

        if not normalized_query or normalized_query.lower() == "unknown":
            localization_error = "QUERY_GENERATION_FAILED"
        else:
            try:
                matching_functions = self.code_searcher.weggli_get_found_with_code(generated_query)
            except Exception as exc:
                localization_error = f"QUERY_ERROR: {exc}"

        return {
            "matching_functions": matching_functions,
            "generated_query": generated_query,
            "localization_error": localization_error,
        }

    def localize_candidates_for_spec(self, func_name: str, target_spec: str) -> dict:
        """Run weggli-based localization for a specification and return candidate functions."""
        print(f"  🔎 Localizing candidates for {func_name}...")

        localization_result = self.localize_candidates_for_spec_with_metadata(func_name, target_spec)
        matching_functions = localization_result["matching_functions"]
        localization_error = localization_result["localization_error"]
        if localization_error and not matching_functions:
            print(f"    ⚠️  Candidate localization failed: {localization_error}")
            return {}

        if not matching_functions:
            print("    📊 No matching functions found")
            return {}

        print(f"    📊 Found {len(matching_functions)} matching code locations")
        return matching_functions
    
    def analyze_code_violation_worker(self, args):
        match_info, predicate, func_name, thread_id = args
        match_name, match_code = match_info
        
        try:
            llm_client = self.get_llm_client(thread_id)
            
            system_prompt = self.prompt_loader.get_step3_prompt(
                "analyze_violation_system",
                func_name=func_name
            )
            user_prompt = self.prompt_loader.get_step3_prompt(
                "analyze_violation_user",
                predicate=predicate,
                match_name=match_name,
                match_code=match_code
            )
            
            response, input_tokens, output_tokens = llm_client.send_message_with_tokens(user_prompt, system_prompt)
            response_upper = response.upper().strip()
            
            is_violation = False
            if "YES" in response_upper:
                is_violation = True
            elif "UNCERTAIN" in response_upper:
                is_violation = True  # Conservative approach
            
            confidence = "LOW"
            if "HIGH" in response_upper:
                confidence = "HIGH"
            elif "MEDIUM" in response_upper:
                confidence = "MEDIUM"
            
            return {
                'match_name': match_name,
                'match_code': match_code,
                'is_violation': is_violation,
                'analysis': response.strip(),
                'confidence': confidence,
                'input_tokens': input_tokens,
                'output_tokens': output_tokens,
                'error': None
            }
            
        except Exception as e:
            return {
                'match_name': match_name,
                'match_code': match_code,
                'is_violation': False,
                'analysis': f"Error during analysis: {str(e)}",
                'confidence': 'ERROR',
                'input_tokens': 0,
                'output_tokens': 0,
                'error': str(e)
            }
    
    def detect_violations_direct(self, func_name: str, target_spec: str, predicate_spec: str) -> dict:
        print(f"  🔎 Detecting violations for {func_name}...")
        
        weggli_query = self.generate_weggli_query(func_name, target_spec)
        
        matching_functions = self.code_searcher.weggli_get_found_with_code(weggli_query)
        
        if not matching_functions:
            print(f"    📊 No matching functions found")
            return {
                'total_matches': 0,
                'violations_detected': 0,
                'violation_details': [],
                'llm_analyses': [],
                'weggli_query': weggli_query
            }
        
        print(f"    📊 Found {len(matching_functions)} matching code locations")

        if self.candidate_function_allowlist:
            original_count = len(matching_functions)
            matching_functions = filter_candidate_matches_for_review(
                matching_functions,
                self.candidate_function_allowlist,
            )
            print(
                f"    🎯 Reviewer buggy-function filter active: "
                f"{len(matching_functions)}/{original_count} matches kept"
            )
            if not matching_functions:
                return {
                    'total_matches': 0,
                    'violations_detected': 0,
                    'violation_details': [],
                    'llm_analyses': [],
                    'weggli_query': weggli_query
                }
        
        matches_to_analyze = list(matching_functions.items())[:self.max_matches_to_analyze]
        
        worker_args = []
        for i, match_info in enumerate(matches_to_analyze):
            thread_id = i % self.max_workers
            worker_args.append((match_info, predicate_spec, func_name, thread_id))
        
        # Analyze matches for violations using thread pool
        violations = []
        all_analyses = []
        
        print(f"    🚀 Starting parallel analysis with {self.max_workers} threads...")
        start_time = time.time()
        
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_to_args = {
                executor.submit(self.analyze_code_violation_worker, args): args 
                for args in worker_args
            }
            
            for i, future in enumerate(as_completed(future_to_args), 1):
                try:
                    result = future.result()
                    
                    self.update_token_usage(result.get('input_tokens', 0), result.get('output_tokens', 0))
                    
                    self.thread_safe_print(
                        f"      [{i}/{len(matches_to_analyze)}] Analyzed: {result['match_name'][:50]}... "
                        f"{'🚨 VIOLATION' if result['is_violation'] else '✅ OK'}"
                    )
                    
                    analysis_info = {
                        'function_name': result['match_name'],
                        'code_snippet': result['match_code'][:500] + "..." if len(result['match_code']) > 500 else result['match_code'],
                        'has_violation': result['is_violation'],
                        'analysis': result['analysis'],
                        'confidence': result['confidence']
                    }
                    
                    all_analyses.append(analysis_info)
                    
                    if result['is_violation']:
                        violations.append(analysis_info)
                    
                    if result['error']:
                        self.thread_safe_print(f"        ⚠️  Warning: {result['error']}")
                
                except Exception as e:
                    self.thread_safe_print(f"        ❌ Error processing task: {e}")
        
        elapsed_time = time.time() - start_time
        print(f"    ⏱️  Parallel analysis completed in {elapsed_time:.2f} seconds")
        print(f"    📊 {func_name}: {len(violations)}/{len(matches_to_analyze)} violations detected")
        
        return {
            'total_matches': len(matching_functions),
            'violations_detected': len(violations),
            'violation_details': violations,
            'llm_analyses': all_analyses,
            'weggli_query': weggli_query
        }

    def process_single_specification(self, spec_row):
        """Perform bug detection for a single specification, returning individual violation records"""
        hexsha = spec_row['hexsha']
        target_function = spec_row.get('similar_target', '')
        # specification is combination of spec_target and spec_predicate
        spec_target = spec_row.get('spec_target', '')
        spec_predicate = spec_row.get('spec_predicate', '')
        specification = f"{spec_target} | {spec_predicate}" if spec_target and spec_predicate else ''
        
        print(f"Processing {hexsha[:12]} - {target_function}...")
        
        # Track token usage for this specification
        spec_start_input_tokens = self.total_input_tokens
        spec_start_output_tokens = self.total_output_tokens
        
        json_result = {
            'hexsha': hexsha,
            'target_function': target_function,
            'specification': specification,
            'spec_target': spec_target,
            'spec_predicate': spec_predicate,
            'similarity_score': spec_row.get('similarity_score', 0.0),
            'weggli_query': '',
            'total_matches': 0,
            'violations': [],
            'token_usage': {
                'input_tokens': 0,
                'output_tokens': 0,
                'total_tokens': 0
            }
        }
        
        base_result = {
            'hexsha': hexsha,
            'target_function': target_function,
            'specification': specification,
            'spec_target': spec_target,
            'spec_predicate': spec_predicate,
            'similarity_score': spec_row.get('similarity_score', 0.0),
            'original_target': spec_row.get('target', ''),  # Use 'target' as original_target
            'original_predicate': spec_row.get('predicate', ''),  # Use 'predicate' as original_predicate
            'generalized_target': spec_row.get('generalized_target', ''),
            'generalized_predicate': spec_row.get('generalized_predicate', ''),
            'commit_description': spec_row.get('commit_description', ''),
            'timestamp': datetime.now().isoformat(),
        }
        
        # Use direct detection with step4 specification data
        detection_results = self.detect_violations_direct(
            target_function, 
            base_result['spec_target'], 
            base_result['spec_predicate']
        )
        
        violations = detection_results.get('violation_details', [])
        weggli_query = detection_results.get('weggli_query', '')
        total_matches = detection_results.get('total_matches', 0)
        
        # Calculate token usage for this specification
        spec_input_tokens = self.total_input_tokens - spec_start_input_tokens
        spec_output_tokens = self.total_output_tokens - spec_start_output_tokens
        spec_total_tokens = spec_input_tokens + spec_output_tokens
        
        json_result['weggli_query'] = weggli_query
        json_result['total_matches'] = total_matches
        json_result['token_usage'] = {
            'input_tokens': spec_input_tokens,
            'output_tokens': spec_output_tokens,
            'total_tokens': spec_total_tokens
        }
        
        with self.results_lock:
            # If no violations found, return one record with no violation
            if not violations:
                json_result['violations'] = []
                self.json_results.append(json_result)
                
                no_violation_result = base_result.copy()
                no_violation_result.update({
                    'status': 'completed',
                    'error_message': '',
                    'weggli_query': weggli_query,
                    'total_matches': total_matches,
                    'violations_detected': 0,
                    'violation_function_name': '',
                    'has_violation': False,
                    'analysis': 'No violations found',
                    'confidence': 'N/A',
                    'input_tokens': spec_input_tokens,
                    'output_tokens': spec_output_tokens,
                    'total_tokens': spec_total_tokens
                })
                StatusReporter.print_success(hexsha, f"{total_matches} matches, 0 violations, {spec_total_tokens} tokens")
                return [no_violation_result]
            
            for violation in violations:
                json_result['violations'].append({
                    'function_name': violation.get('function_name', ''),
                    'has_violation': violation.get('has_violation', False),
                    'analysis': violation.get('analysis', ''),
                    'confidence': violation.get('confidence', 'Unknown')
                })
            
            self.json_results.append(json_result)
        
        # Create one record per violation (step4 format)
        violation_records = []
        for violation in violations:
            violation_record = base_result.copy()
            violation_record.update({
                'status': 'completed',
                'error_message': '',
                'weggli_query': weggli_query,
                'total_matches': total_matches,
                'violations_detected': len(violations),
                'violation_function_name': violation.get('function_name', ''),  # The specific function with violation
                'has_violation': violation.get('has_violation', False),
                'analysis': violation.get('analysis', ''),
                'confidence': violation.get('confidence', 'Unknown'),
                'input_tokens': spec_input_tokens,
                'output_tokens': spec_output_tokens,
                'total_tokens': spec_total_tokens
            })
            violation_records.append(violation_record)
        
        StatusReporter.print_success(hexsha, f"{total_matches} matches, {len(violations)} violations, {spec_total_tokens} tokens")
        return violation_records
    
    def save_json_results(self, output_path):
        import re
        timestamp_pattern = r'_\d{8}_\d{6}\.csv$'
        
        if re.search(timestamp_pattern, output_path):
            # Already has timestamp, just change extension
            json_path = output_path.replace('.csv', '.json')
        else:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            base_name = output_path.replace('.csv', '')
            json_path = f"{base_name}_{timestamp}.json"
        
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(self.json_results, f, indent=2, ensure_ascii=False)
        print(f"JSON results saved to: {json_path}")
        return json_path
    
    def convert_json_to_csv(self, json_path, csv_path):
        """Convert JSON results to CSV format - one row per violation"""
        csv_rows = []
        
        for result in self.json_results:
            # Only process results that have violations
            if result['violations']:
                # Create one row for each violation
                for violation in result['violations']:
                    csv_rows.append({
                        'hexsha': result['hexsha'],
                        'target_function': result['target_function'],
                        'spec_target': result['spec_target'],
                        'spec_predicate': result['spec_predicate'],
                        'weggli_query': result['weggli_query'],
                        'total_matches': result['total_matches'],
                        'violation_function_name': violation.get('function_name', ''),
                        'has_violation': violation.get('has_violation', False),
                        'analysis': violation.get('analysis', ''),
                        'confidence': violation.get('confidence', 'Unknown')
                    })
        
        if csv_rows:
            df = pd.DataFrame(csv_rows)
            df.to_csv(csv_path, index=False, encoding='utf-8')
            print(f"Violations CSV saved to: {csv_path} ({len(csv_rows)} violations)")
        else:
            print(f"No violations found - empty CSV created: {csv_path}")
            empty_df = pd.DataFrame(columns=[
                'hexsha', 'target_function', 'spec_target', 'spec_predicate', 
                'weggli_query', 'total_matches', 'violation_function_name', 
                'has_violation', 'analysis', 'confidence'
            ])
            empty_df.to_csv(csv_path, index=False, encoding='utf-8')
        
        return csv_path
    
    def load_existing_progress(self, output_csv):
        """Load existing progress from output files to enable resume functionality"""
        processed_hexshas = set()
        
        json_path = output_csv.replace('.csv', '.json')
        if os.path.exists(json_path):
            try:
                with open(json_path, 'r', encoding='utf-8') as f:
                    existing_results = json.load(f)
                self.json_results = existing_results
                processed_hexshas.update(result['hexsha'] for result in existing_results)
                print(f"📂 Loaded {len(existing_results)} existing JSON results from {json_path}")
            except Exception as e:
                print(f"⚠️  Warning: Could not load existing JSON results: {e}")
        
        if os.path.exists(output_csv):
            try:
                existing_df = pd.read_csv(output_csv)
                if 'hexsha' in existing_df.columns:
                    csv_hexshas = set(existing_df['hexsha'].unique())
                    processed_hexshas.update(csv_hexshas)
                    print(f"📂 Found {len(csv_hexshas)} unique commits in existing CSV: {output_csv}")
            except Exception as e:
                print(f"⚠️  Warning: Could not load existing CSV results: {e}")
        
        return processed_hexshas
    
    def save_progress_checkpoint(self, output_csv, current_results):
        if not current_results:
            return
            
        try:
            json_path = output_csv.replace('.csv', '.json')
            with open(json_path, 'w', encoding='utf-8') as f:
                json.dump(self.json_results, f, indent=2, ensure_ascii=False)
            
            CSVProcessor.save_results_to_csv(current_results, output_csv)
            print(f"💾 Progress checkpoint saved: {len(current_results)} records")
            
        except Exception as e:
            print(f"⚠️  Warning: Could not save checkpoint: {e}")
    
    def print_token_usage_summary(self):
        print("\n" + "=" * 60)
        print("📊 TOKEN USAGE SUMMARY")
        print("=" * 60)
        print(f"  Total Input Tokens:  {self.total_input_tokens:,}")
        print(f"  Total Output Tokens: {self.total_output_tokens:,}")
        print(f"  Total Tokens:        {self.total_tokens:,}")
        print("=" * 60)
        
        if self.json_results:
            num_specs = len(self.json_results)
            avg_input = self.total_input_tokens / num_specs
            avg_output = self.total_output_tokens / num_specs
            avg_total = self.total_tokens / num_specs
            
            print(f"📈 AVERAGE PER SPECIFICATION ({num_specs} specs)")
            print("=" * 60)
            print(f"  Avg Input Tokens:  {avg_input:,.1f}")
            print(f"  Avg Output Tokens: {avg_output:,.1f}")
            print(f"  Avg Total Tokens:  {avg_total:,.1f}")
            print("=" * 60)

    def process_step4_results(self, step4_csv, output_csv, resume=True, checkpoint_interval=10):
        """Process all specifications from Step 4 results with resume capability"""
        print(f"Reading Step 4 specification results from: {step4_csv}")
        
        try:
            df = pd.read_csv(step4_csv)
            print(f"✅ Loaded {len(df)} specifications")
        except Exception as e:
            print(f"❌ Error reading CSV file: {e}")
            return
        
        required_columns = ['hexsha', 'similar_target', 'spec_target', 'spec_predicate']
        missing_columns = [col for col in required_columns if col not in df.columns]
        
        if missing_columns:
            print(f"❌ Missing required columns: {missing_columns}")
            print(f"Available columns: {list(df.columns)}")
            return
        
        # Filter out specifications without target or predicate
        valid_df = df[(df['spec_target'].notna()) & (df['spec_predicate'].notna()) & 
                     (df['spec_target'] != '') & (df['spec_predicate'] != '')]

        if self.spec_target_allowlist:
            original_valid = len(valid_df)
            valid_df = valid_df[valid_df['similar_target'].isin(self.spec_target_allowlist)]
            print(
                f"🎯 Reviewer spec-target filter active: "
                f"{len(valid_df)}/{original_valid} specifications kept"
            )
        
        if len(valid_df) == 0:
            print("No valid specifications found (missing spec_target or spec_predicate)")
            return
        
        # Load existing progress if resume is enabled
        processed_hexshas = set()
        all_violation_records = []
        
        if resume:
            processed_hexshas = self.load_existing_progress(output_csv)
            print(f"🔄 Resume mode: {len(processed_hexshas)} commits already processed")
            
            # Load existing detailed results for final save
            if os.path.exists(output_csv):
                try:
                    existing_df = pd.read_csv(output_csv)
                    all_violation_records = existing_df.to_dict('records')
                    print(f"📂 Loaded {len(all_violation_records)} existing detailed records")
                except Exception as e:
                    print(f"⚠️  Warning: Could not load existing detailed records: {e}")
        
        if processed_hexshas:
            remaining_df = valid_df[~valid_df['hexsha'].isin(processed_hexshas)]
            print(f"⏭️  Skipping {len(valid_df) - len(remaining_df)} already processed specifications")
        else:
            remaining_df = valid_df
        
        if len(remaining_df) == 0:
            print("🎉 All specifications have been processed!")
            return
        
        print(f"Processing {len(remaining_df)} remaining specifications...")
        print(f"🚀 Using {self.max_workers} threads for parallel LLM requests")
        
        # Add timestamp to output filename only if not already present
        import re
        timestamp_pattern = r'_\d{8}_\d{6}\.csv$'
        
        if re.search(timestamp_pattern, output_csv):
            # Already has timestamp, use as is
            final_csv = output_csv
        else:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            base_name = output_csv.replace('.csv', '')
            final_csv = f"{base_name}_{timestamp}.csv"
        
        new_violation_records = []
        for i, (_, row) in enumerate(remaining_df.iterrows(), 1):
            target_info = row.get('similar_target', f"row_{i}")
            total_processed = len(processed_hexshas) + i
            total_specs = len(valid_df)
            
            print(f"[{total_processed}/{total_specs}] {row['hexsha'][:12]} - {target_info}")
            
            try:
                violation_records = self.process_single_specification(row)
                new_violation_records.extend(violation_records)
                
                if i % checkpoint_interval == 0:
                    current_all_records = all_violation_records + new_violation_records
                    self.save_progress_checkpoint(final_csv, current_all_records)
                    
            except Exception as e:
                print(f"❌ Error processing {row['hexsha'][:12]}: {e}")
                continue
        
        all_violation_records.extend(new_violation_records)
        
        json_path = self.save_json_results(final_csv)
        
        # 2. Convert JSON to simplified CSV
        if re.search(timestamp_pattern, final_csv):
            simplified_csv = final_csv.replace('.csv', '_simplified.csv')
        else:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            base_name = final_csv.replace('.csv', '')
            simplified_csv = f"{base_name}_simplified_{timestamp}.csv"
        
        self.convert_json_to_csv(json_path, simplified_csv)
        
        # 3. Save legacy CSV format (detailed records)
        CSVProcessor.save_results_to_csv(all_violation_records, final_csv)
        StatusReporter.print_bug_detection_summary(all_violation_records)
        
        self.print_token_usage_summary()
        
        print(f"\nStep 3 output files generated:")
        print(f"  📄 JSON format: {json_path}")
        print(f"  📊 Simplified CSV: {simplified_csv}")
        print(f"  📊 Detailed CSV: {final_csv}")


def find_step2_file(hexsha_or_file):
    """
    Find step2 result file based on hexsha or return the file if it already exists.
    
    Args:
        hexsha_or_file: Either a hexsha string or a file path
        
    Returns:
        Path to the step2 file
    """
    import os
    import glob
    
    # If it's already a file path and exists, return it
    if hexsha_or_file.endswith('.csv') and os.path.exists(hexsha_or_file):
        return hexsha_or_file
    
    # Check if it looks like a hexsha (8+ hex characters, no .csv extension)
    if not hexsha_or_file.endswith('.csv') and len(hexsha_or_file) >= 8:
        hexsha_short = hexsha_or_file[:12]  # Use first 12 characters
        
        # Look for step2 files in results directory
        results_dir = Path(__file__).parent.parent / 'results'
        pattern = f"*step2*{hexsha_short}*.csv"
        matching_files = glob.glob(str(results_dir / pattern))
        
        if matching_files:
            # Sort by modification time, return the newest
            latest_file = max(matching_files, key=os.path.getmtime)
            print(f"🔍 Found step2 file: {os.path.basename(latest_file)}")
            return latest_file
        else:
            raise FileNotFoundError(f"No step2 result file found for hexsha {hexsha_short} in {results_dir}")
    
    # If we get here, assume it's a file path
    if not os.path.exists(hexsha_or_file):
        raise FileNotFoundError(f"File not found: {hexsha_or_file}")
    
    return hexsha_or_file


def main():
    parser = argparse.ArgumentParser(description='Specification-based Bug Detection using Step 4 Results (Multi-threaded)')
    parser.add_argument('step4_csv', help='Path to Step 4 specification results CSV file')
    parser.add_argument('--kernel-path', default='/root/linux',
                       help='Path to Linux kernel source code')
    parser.add_argument('--output', default=None,
                       help='Output CSV file (default: auto-generated based on input)')
    parser.add_argument('--model', default='claude-sonnet-4-20250514', help='Model to use for violation detection')
    parser.add_argument('--max-matches', type=int, default=100,
                       help='Maximum number of matching functions to analyze (default: 20)')
    parser.add_argument('--max-workers', type=int, default=10,
                       help='Maximum number of worker threads for parallel LLM requests (default: 4)')
    parser.add_argument('--no-resume', action='store_true', 
                       help='Disable resume functionality (start from beginning)')
    parser.add_argument('--checkpoint-interval', type=int, default=10,
                       help='Save progress checkpoint every N specifications (default: 10)')
    
    args = parser.parse_args()
    
    if not os.path.exists(args.step4_csv):
        print(f"❌ Error: File {args.step4_csv} not found")
        sys.exit(1)
    
    print(f"📂 Using step4 file: {args.step4_csv}")
    
    # Auto-generate output filename if not provided
    if not args.output:
        base_name = os.path.basename(args.step4_csv)
        if 'step4' in base_name:
            output_name = base_name.replace('step4', 'bug_detection_threaded').replace('.csv', f'_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv')
        else:
            output_name = f"bug_detection_threaded_{base_name.replace('.csv', '')}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        
        results_dir = Path(__file__).parent.parent / 'results'
        results_dir.mkdir(exist_ok=True)
        args.output = str(results_dir / output_name)
    
    print(f"📝 Output will be saved to: {args.output}")
    
    if args.no_resume:
        print("🔄 Resume disabled - starting from beginning")
    else:
        print("🔄 Resume enabled - will skip already processed specifications")
    
    print(f"🚀 Multi-threading enabled: {args.max_workers} workers")
    print(f"📊 Max matches to analyze: {args.max_matches}")
    
    detector = ThreadedBugDetector(
        args.kernel_path, 
        model=args.model, 
        max_matches_to_analyze=args.max_matches,
        max_workers=args.max_workers,
    )
    detector.process_step4_results(args.step4_csv, args.output, 
                                 resume=not args.no_resume, 
                                 checkpoint_interval=args.checkpoint_interval)


if __name__ == '__main__':
    main()
