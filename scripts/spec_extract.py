#!/usr/bin/env python3
import argparse
import pandas as pd
import json
import sys
import os
from pathlib import Path
from datetime import datetime
import traceback
import requests
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


class SpecExtractor:
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


    
    def extract_target_predicate(self, hexsha: str) -> dict:
        print("🎯 Stage 1: Abstracting Target and Predicate")
        
        # If hexsha is provided, fetch patch and commit message
        patch_content = ""
        commit_message = ""
        
        print(f"  📋 Fetching patch for commit: {hexsha}")
        patch_content = self.patch_extractor.get_full_function_diff(hexsha)
        # For commit message, we'll use git directly
        
        result = subprocess.run(
            ['git', 'show', hexsha, '--format=%B', '--no-patch'],
            cwd=self.kernel_path,
            capture_output=True,
            text=True
        )

        commit_message = result.stdout.strip() if result.returncode == 0 else ""
        commit_message = get_clean_message(commit_message)

        print(f"  📝 Commit message: {commit_message}...")
                
        if not patch_content:
            print(f"  ❌ Failed to get patch for commit {hexsha}")
            return {}
            
        print(f"  ✅ Retrieved patch ({len(patch_content)} characters)")
   
        # Load prompts and get LLM response
        system_prompt = self.prompt_loader.get_step1_prompt("extract_patterns_system")
        user_prompt = self.prompt_loader.get_step1_prompt(
            "extract_patterns_user",
            patch_content=patch_content,
            commit_message=commit_message
        )

        response, input_tokens, output_tokens = self.llm_client.send_message_with_tokens(user_prompt, system_prompt)
        
        self.total_input_tokens += input_tokens
        self.total_output_tokens += output_tokens
        self.total_tokens += (input_tokens + output_tokens)
        self.api_calls_count += 1
        
        result = self._parse_json_response(response)
        
        if result:
            print(f"  ✅ Target: {result.get('target_description', '')}")
            print(f"  ✅ Predicate: {result.get('predicate_description', '')}")
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

    
    
   
    def process_single_commit(self, hexsha, description=""):
        print(f"Processing {hexsha[:12]}...")
        
        result = ResultFormatter.create_base_result(hexsha, description, {
            'target': '',
            'predicate': '',
            'input_tokens': 0,
            'output_tokens': 0,
            'total_tokens': 0
        })
        
        stage1_result = self.extract_target_predicate(hexsha)
        
        if not stage1_result:
            return ResultFormatter.mark_failed(result, "Failed to extract target and predicate")
        
        target = stage1_result.get('target_description', '')
        predicate = stage1_result.get('predicate_description', '')
        commit_message = stage1_result.get('commit_message', description)  # Use actual commit message
        
        if not target or not predicate:
            return ResultFormatter.mark_failed(result, "Failed to extract target and predicate from result")
        
        result['target'] = target
        result['predicate'] = predicate
        result['description'] = commit_message  # Update description with actual commit message
        result['input_tokens'] = stage1_result.get('input_tokens', 0)
        result['output_tokens'] = stage1_result.get('output_tokens', 0)
        result['total_tokens'] = stage1_result.get('total_tokens', 0)
        
        ResultFormatter.mark_completed(result)
        return result


    def process_csv(self, input_csv, output_csv):
        print(f"Reading commits from: {input_csv}")
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        base_name = output_csv.replace('.csv', '')
        final_csv = f"{base_name}_{timestamp}.csv"
        
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
        commits = df.to_dict('records')
        
        pending_commits = [c for c in commits if c['hexsha'] not in existing_results]
        
        print(f"📊 Total commits: {len(commits)}, Already processed: {len(existing_results)}, Pending: {len(pending_commits)}")
        
        for hexsha, result in existing_results.items():
            self.results.append(result.to_dict() if hasattr(result, 'to_dict') else result)
        
        if not pending_commits:
            print("✅ All commits already processed!")
            return
        
        # Process pending commits with periodic saving
        try:
            for i, commit in enumerate(pending_commits, 1):
                hexsha = commit['hexsha']
                description = commit.get('description', '')
                
                print(f"[{i}/{len(pending_commits)}] {hexsha}")
                result = self.process_single_commit(hexsha, description)
                self.results.append(result)
                
                if i % 5 == 0:
                    self._save_progress(final_csv)
                    print(f"💾 Progress saved ({i}/{len(pending_commits)} processed)")
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
        CSVProcessor.save_results_to_csv(self.results, final_csv)
        StatusReporter.print_pipeline_summary(self.results)
        
        self.print_token_statistics()
        
        print(f"✅ All processing complete! Results saved to: {final_csv}")
    
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
            num_commits = len([r for r in self.results if r.get('status') == 'completed'])
            if num_commits > 0:
                avg_input_per_commit = self.total_input_tokens / num_commits
                avg_output_per_commit = self.total_output_tokens / num_commits
                avg_total_per_commit = self.total_tokens / num_commits
                
                print("📋 AVERAGE PER COMMIT:")
                print(f"  Successful Commits:   {num_commits}")
                print(f"  Avg Input Tokens:     {avg_input_per_commit:,.1f}")
                print(f"  Avg Output Tokens:    {avg_output_per_commit:,.1f}")
                print(f"  Avg Total Tokens:     {avg_total_per_commit:,.1f}")
        
        print("=" * 80)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('input_csv', nargs='?', help='Input CSV file with commit hexsha')
    parser.add_argument('--output', default='step1_specifcation_extraction.csv',
                       help='Output CSV file')
    parser.add_argument('--test', help='Test single commit hexsha directly')
    parser.add_argument('--kernel-path', default='./linux',
                       help='Path to the Linux kernel source tree')
    parser.add_argument('--model', default=None,
                       help='LLM model to use for extraction (defaults to config default)')
    
    args = parser.parse_args()
    
    searcher = SpecExtractor(args.kernel_path, model=args.model)
    
    if args.test:
        print(f"🧪 Testing single commit: {args.test}")
        result = searcher.process_single_commit(args.test)
        print("\n" + "="*50)
        print("📊 Test Result:")
        print("="*50)
        for key, value in result.items():
            print(f"{key}: {value}")
        
        # Print token statistics for test mode
        print("\n")
        searcher.print_token_statistics()
        return
    
    if not args.input_csv:
        parser.error("input_csv is required when not using --test mode")
    
    searcher.process_csv(args.input_csv, args.output)


if __name__ == '__main__':
    main()
