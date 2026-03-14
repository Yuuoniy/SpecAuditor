#!/usr/bin/env python3
import argparse
import pandas as pd
import json
import sys
import os
from pathlib import Path
from datetime import datetime
import traceback
import subprocess

try:
    from scripts.utils.artifact_utils import configure_script_imports
except ImportError:
    from utils.artifact_utils import configure_script_imports

configure_script_imports(__file__)

# Import utils directly - no dependency on simplified_pipeline
# Import prompt loader and shared utilities
from prompt_loader import PromptLoader
from shared_utils import CSVProcessor, ResultFormatter, StatusReporter, parse_llm_json_response
from openai_client import OpenAIClient
from get_patch_full_diff import PatchFullDiffExtractor


prefixes = [
    "Signed-off-by:", "Reported-by:", "Fixes:", "Link:", "Suggested-by:", "cc:", 
    "Tested-by:", "Acked-by:", "Reviewed-by:", "CC:", "Cc:", "Requested-by:", 
    "Reported by:", "(Merged from", "Reported-and-tested-by:", "Closes:", 
    "Message-Id:", "Reviewed by:", "Sponsored by:", "Differential revision:", 
    "Submitted by", "Co-developed-by:", "Co-authored-by:", "Stable-dep-of:",
    "Upstream-Status:", "CVE:", "References:", "Bug:", "Change-Id:", "Cherry-picked from"
]

def get_clean_message(msg):
    lines = msg.splitlines()
    lines_filter = list(filter(lambda x: not x.startswith(tuple(prefixes)), lines))
    message = "\n".join(lines_filter)
    return message


class SpecGeneralizer:
    def __init__(self, kernel_path, model=None):
        self.kernel_path = kernel_path
        self.results = []
        self.prompt_loader = PromptLoader()
        self.llm_client = OpenAIClient(model=model) if model else OpenAIClient()
        self.patch_extractor = PatchFullDiffExtractor(kernel_path)
        
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.total_tokens = 0
        self.api_calls_count = 0
    
    def _parse_json_response(self, response: str) -> dict:
        try:
            return parse_llm_json_response(response)
        except (json.JSONDecodeError, ValueError) as e:
            print(f"  ⚠️  JSON parse error: {e}")
            print(f"  Raw response: {response[:200]}...")
            return {}

    def generalize_specification(self, hexsha: str, original_target: str, original_predicate: str) -> dict:
        print("🔄 Stage 2: Generalizing Specification")
        
        print(f"  📋 Fetching patch for commit: {hexsha}")
        patch_content = self.patch_extractor.get_full_function_diff(hexsha)
        
        result = subprocess.run(
            ['git', 'show', hexsha, '--format=%B', '--no-patch'],
            cwd=self.kernel_path,
            capture_output=True,
            text=True
        )

        commit_message = result.stdout.strip() if result.returncode == 0 else ""
        commit_message = get_clean_message(commit_message)

        print(f"  📝 Commit message: {commit_message[:100]}...")
                
        if not patch_content:
            print(f"  ❌ Failed to get patch for commit {hexsha}")
            return {}
            
        print(f"  ✅ Retrieved patch ({len(patch_content)} characters)")
   
        # Load step2 prompts and get LLM response
        system_prompt = self.prompt_loader.get_step2_prompt("generalize_system")
        user_prompt = self.prompt_loader.get_step2_prompt(
            "generalize_user",
            patch_content=patch_content,
            commit_message=commit_message,
            original_target=original_target,
            original_predicate=original_predicate
        )

        response, input_tokens, output_tokens = self.llm_client.send_message_with_tokens(user_prompt, system_prompt)
        
        self.total_input_tokens += input_tokens
        self.total_output_tokens += output_tokens
        self.total_tokens += (input_tokens + output_tokens)
        self.api_calls_count += 1
        
        result = self._parse_json_response(response)
        
        if result:
            print(f"  ✅ Generalized Target: {result.get('generalized_target', '')}")
            print(f"  ✅ Generalized Predicate: {result.get('generalized_predicate', '')}")
            print(f"  📊 Tokens - Input: {input_tokens:,}, Output: {output_tokens:,}, Total: {input_tokens + output_tokens:,}")
            # Add commit message and token info to result for later use
            result['commit_message'] = commit_message
            result['input_tokens'] = input_tokens
            result['output_tokens'] = output_tokens
            result['total_tokens'] = input_tokens + output_tokens
            return result
        else:
            print("  ❌ Failed to parse LLM response")
            return {}

    def process_single_row(self, row_data: dict) -> dict:
        hexsha = row_data['hexsha']
        original_target = row_data.get('target', '')
        original_predicate = row_data.get('predicate', '')
        
        print(f"Processing {hexsha[:12]}...")
        
        original_target = str(original_target).strip() if original_target and str(original_target) != 'nan' else ''
        original_predicate = str(original_predicate).strip() if original_predicate and str(original_predicate) != 'nan' else ''
        error_message = str(row_data.get('error_message', '')).strip() if row_data.get('error_message') and str(row_data.get('error_message')) != 'nan' else ''
        
        result = row_data.copy()
        
        if not original_target or not original_predicate or error_message:
            print(f"  ⚠️  Skipping {hexsha[:12]} - original extraction failed or incomplete")
            result['generalized_target'] = ''
            result['generalized_predicate'] = ''
            result['generalization_status'] = 'skipped'
            result['input_tokens'] = 0
            result['output_tokens'] = 0
            result['total_tokens'] = 0
            return result
        
        generalization_result = self.generalize_specification(hexsha, original_target, original_predicate)
        
        if generalization_result:
            result['generalized_target'] = generalization_result.get('generalized_target', '')
            result['generalized_predicate'] = generalization_result.get('generalized_predicate', '')
            result['generalization_status'] = 'completed'
            result['input_tokens'] = generalization_result.get('input_tokens', 0)
            result['output_tokens'] = generalization_result.get('output_tokens', 0)
            result['total_tokens'] = generalization_result.get('total_tokens', 0)
            # Update description with actual commit message if available
            if 'commit_message' in generalization_result:
                result['description'] = generalization_result['commit_message']
        else:
            result['generalized_target'] = ''
            result['generalized_predicate'] = ''
            result['generalization_status'] = 'failed'
            result['input_tokens'] = 0
            result['output_tokens'] = 0
            result['total_tokens'] = 0
        
        return result

    def _generate_output_filename(self, input_csv, output_csv):
        import re
        
        match = re.search(r'step1_specifcation_extraction_(\d{8}_\d{6})\.csv$', input_csv)
        
        if match:
            timestamp_suffix = match.group(1)
            return f"step2_specifcation_generalization_{timestamp_suffix}.csv"
        else:
            # Fallback to default behavior if pattern doesn't match
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            base_name = output_csv.replace('.csv', '')
            return f"{base_name}_{timestamp}.csv"

    def process_csv(self, input_csv, output_csv=None):
        """Process all rows from the spec extraction CSV file with resume capability"""
        print(f"Reading spec extraction results from: {input_csv}")
        
        final_csv = output_csv if output_csv else self._generate_output_filename(input_csv, "step2_generalized_specifications.csv")
        
        existing_results = {}
        if os.path.exists(final_csv):
            print(f"📁 Found existing results file: {final_csv}")
            try:
                existing_df = pd.read_csv(final_csv)
                existing_results = {row['hexsha']: row for _, row in existing_df.iterrows()}
                print(f"📋 Loaded {len(existing_results)} existing results")
            except Exception as e:
                print(f"⚠️  Warning: Could not load existing results: {e}")
        
        # Read input CSV (spec extraction results)
        df = pd.read_csv(input_csv, comment='#')
        rows = df.to_dict('records')
        
        pending_rows = [r for r in rows if r['hexsha'] not in existing_results]
        
        print(f"📊 Total rows: {len(rows)}, Already processed: {len(existing_results)}, Pending: {len(pending_rows)}")
        
        for hexsha, result in existing_results.items():
            self.results.append(result.to_dict() if hasattr(result, 'to_dict') else result)
        
        if not pending_rows:
            print("✅ All rows already processed!")
            return
        
        # Process pending rows with periodic saving
        try:
            for i, row in enumerate(pending_rows, 1):
                hexsha = row['hexsha']
                print(f"[{i}/{len(pending_rows)}] {hexsha}")
                result = self.process_single_row(row)
                self.results.append(result)
                
                # Save progress every 3 commits (generalization is slower)
                if i % 3 == 0:
                    self._save_progress(final_csv)
                    print(f"💾 Progress saved ({i}/{len(pending_rows)} processed)")
                    if self.api_calls_count > 0:
                        print(f"   📊 Token usage so far - Total: {self.total_tokens:,} (Input: {self.total_input_tokens:,}, Output: {self.total_output_tokens:,})")
        
        except KeyboardInterrupt:
            print("\n⚠️  Process interrupted by user")
            self._save_progress(final_csv)
            print(f"💾 Progress saved to: {final_csv}")
            print("🔄 You can resume by running the same command again")
            return
        except Exception as e:
            print(f"\n❌ Error occurred: {e}")
            self._save_progress(final_csv)
            print(f"💾 Progress saved to: {final_csv}")
            raise
        
        self._save_progress(final_csv)
        
        total = len(self.results)
        completed = sum(1 for r in self.results if r.get('generalization_status') == 'completed')
        failed = sum(1 for r in self.results if r.get('generalization_status') == 'failed')
        skipped = sum(1 for r in self.results if r.get('generalization_status') == 'skipped')
        
        print(f"\n✅ Generalization complete!")
        print(f"📊 Summary: {completed} completed, {failed} failed, {skipped} skipped / {total} total")
        
        self.print_token_statistics()
        
        print(f"💾 Results saved to: {final_csv}")
    
    def _save_progress(self, output_file):
        try:
            results_df = pd.DataFrame(self.results)
            results_df.to_csv(output_file, index=False)
        except Exception as e:
            print(f"⚠️  Failed to save progress: {e}")
    
    def print_token_statistics(self):
        print("\n" + "=" * 80)
        print("📊 TOKEN USAGE STATISTICS")
        print("=" * 80)
        print(f"  Total API Calls:      {self.api_calls_count:,}")
        print(f"  Total Input Tokens:   {self.total_input_tokens:,}")
        print(f"  Total Output Tokens:  {self.total_output_tokens:,}")
        print(f"  Total Tokens:         {self.total_tokens:,}")
        print("-" * 80)
        
        if self.api_calls_count > 0:
            avg_input = self.total_input_tokens / self.api_calls_count
            avg_output = self.total_output_tokens / self.api_calls_count
            avg_total = self.total_tokens / self.api_calls_count
            
            print("📈 AVERAGE PER API CALL:")
            print(f"  Avg Input Tokens:     {avg_input:,.1f}")
            print(f"  Avg Output Tokens:    {avg_output:,.1f}")
            print(f"  Avg Total Tokens:     {avg_total:,.1f}")
            print("-" * 80)
        
        if self.results:
            completed_results = [r for r in self.results if r.get('generalization_status') == 'completed']
            if completed_results:
                num_completed = len(completed_results)
                avg_input_per_spec = self.total_input_tokens / num_completed
                avg_output_per_spec = self.total_output_tokens / num_completed
                avg_total_per_spec = self.total_tokens / num_completed
                
                print("📋 AVERAGE PER SPECIFICATION:")
                print(f"  Completed Specs:      {num_completed}")
                print(f"  Avg Input Tokens:     {avg_input_per_spec:,.1f}")
                print(f"  Avg Output Tokens:    {avg_output_per_spec:,.1f}")
                print(f"  Avg Total Tokens:     {avg_total_per_spec:,.1f}")
        
        print("=" * 80)


def main():
    parser = argparse.ArgumentParser(description='Specification Generalization Tool')
    parser.add_argument('input_csv', nargs='?', help='Input CSV file with spec extraction results')
    parser.add_argument('--output', default=None,
                       help='Output CSV file')
    parser.add_argument('--test', help='Test single commit hexsha directly (requires target and predicate)')
    parser.add_argument('--target', help='Original target for test mode')
    parser.add_argument('--predicate', help='Original predicate for test mode')
    parser.add_argument('--kernel-path', default='./linux',
                       help='Path to the Linux kernel source tree')
    parser.add_argument('--model', default=None,
                       help='LLM model to use for generalization (defaults to config default)')
    
    args = parser.parse_args()
    
    generalizer = SpecGeneralizer(args.kernel_path, model=args.model)
    
    # Test mode: process single hexsha with provided target/predicate
    if args.test:
        if not args.target or not args.predicate:
            parser.error("--target and --predicate are required when using --test mode")
        
        print(f"🧪 Testing single commit: {args.test}")
        print(f"Original Target: {args.target}")
        print(f"Original Predicate: {args.predicate}")
        
        result = generalizer.generalize_specification(args.test, args.target, args.predicate)
        print("\n" + "="*50)
        print("📊 Generalization Result:")
        print("="*50)
        for key, value in result.items():
            print(f"{key}: {value}")
        
        # Print token statistics for test mode
        print("\n")
        generalizer.print_token_statistics()
        return
    
    if not args.input_csv:
        parser.error("input_csv is required when not using --test mode")
    
    generalizer.process_csv(args.input_csv, args.output)


if __name__ == '__main__':
    main()
