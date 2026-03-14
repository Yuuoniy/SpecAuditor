#!/usr/bin/env python3
"""
Shared utility functions for the independent pipeline scripts.
Reduces code duplication across step1, step2, and step3.
"""

import json
from datetime import datetime

import pandas as pd


class ResultFormatter:
    
    @staticmethod
    def create_base_result(hexsha, description="", extra_fields=None):
        result = {
            'hexsha': hexsha,
            'description': description,
            'error_message': ''
        }
        if extra_fields:
            result.update(extra_fields)
        return result
    
    @staticmethod
    def mark_completed(result, extra_updates=None):
        if extra_updates:
            result.update(extra_updates)
        return result
    
    @staticmethod
    def mark_failed(result, error_message, extra_updates=None):
        result['error_message'] = error_message
        if extra_updates:
            result.update(extra_updates)
        return result


class StatusReporter:
    
    @staticmethod
    def print_success(hexsha, message):
        print(f"✅ {hexsha[:12]}: {message}")
    
    @staticmethod
    def print_error(hexsha, message):
        print(f"❌ {hexsha[:12]}: {message}")
    
    @staticmethod
    def print_info(message, indent=0):
        prefix = "  " * indent
        print(f"{prefix}{message}")
    
    @staticmethod
    def print_pipeline_summary(results):
        if not results:
            print("No results to summarize")
            return
            
        total = len(results)
        completed = sum(1 for r in results if r.get('status') == 'completed')
        errors = sum(1 for r in results if r.get('status') == 'error')
        
        print(f"\n=== Pipeline Summary ===")
        print(f"Total: {total}")
        print(f"✅ Completed: {completed}")
        print(f"❌ Errors: {errors}")
        
        if errors > 0:
            error_results = [r for r in results if r.get('status') == 'error']
            print(f"\nError details:")
            for result in error_results[:5]:  # Show first 5 errors
                hexsha = result.get('hexsha', 'unknown')[:8]
                error_msg = result.get('error_message', 'No error message')
                print(f"  {hexsha}: {error_msg}")
            if len(error_results) > 5:
                print(f"  ... and {len(error_results) - 5} more errors")
    
    @staticmethod
    def print_bug_detection_summary(violation_records):
        if not violation_records:
            print("No violation records to summarize")
            return
        
        total_violations = len([r for r in violation_records if r.get('has_violation', False)])
        unique_commits = len(set([r['hexsha'] for r in violation_records]))
        errors = len([r for r in violation_records if r['status'] == 'error'])
        
        print(f"\n=== Bug Detection Summary ===")
        print(f"✅ Commits processed: {unique_commits}")
        print(f"📄 Total violation records: {len(violation_records)}")
        print(f"🚨 Total violations found: {total_violations}")
        print(f"❌ Errors: {errors}")


class CSVProcessor:
    
    @staticmethod
    def save_results_to_csv(results, output_csv):
        if not results:
            print(f"No results to save to {output_csv}")
            # Create empty CSV with standard columns for consistency
            empty_df = pd.DataFrame(columns=[
                'hexsha', 'description', 'timestamp', 'status', 'error_message'
            ])
            empty_df.to_csv(output_csv, index=False)
            return
            
        df = pd.DataFrame(results)
        df.to_csv(output_csv, index=False)
        print(f"Results saved to: {output_csv}")
    
    @staticmethod
    def filter_completed_results(csv_file):
        df = pd.read_csv(csv_file)
        completed_df = df[df['status'] == 'completed']
        return df
    
    @staticmethod
    def count_unique_commits(csv_file):
        df = pd.read_csv(csv_file)
        return df['hexsha'].nunique()


def validate_required_fields(data, required_fields, context=""):
    missing_fields = []
    for field in required_fields:
        if field not in data or not data[field]:
            missing_fields.append(field)
    
    if missing_fields:
        context_str = f" in {context}" if context else ""
        raise ValueError(f"Missing required fields{context_str}: {missing_fields}")
    
    return True


def safe_get_nested(data, keys, default=None):
    try:
        for key in keys:
            data = data[key]
        return data
    except (KeyError, TypeError):
        return default


def extract_json_block(content):
    if not isinstance(content, str):
        return None

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


def parse_llm_json_response(response):
    content = str(response or "").strip()

    try:
        if "```json" in content:
            start = content.find("```json") + 7
            end = content.find("```", start)
            content = content[start:end].strip()
        elif content.startswith("```") and content.endswith("```"):
            content = content[3:-3].strip()

        return json.loads(content)
    except Exception:
        candidate = extract_json_block(content)
        if candidate:
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                pass
        raise
