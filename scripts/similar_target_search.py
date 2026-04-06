#!/usr/bin/env python3
"""
Similar Target Search Tool
Searches for similar targets using generalized target descriptions from spec_generalize results.
"""

import argparse
import pandas as pd
import json
import sys
import os
from pathlib import Path
from datetime import datetime
import re

try:
    from scripts.utils.artifact_utils import configure_script_imports, get_chroma_dir
except ImportError:
    from utils.artifact_utils import configure_script_imports, get_chroma_dir

configure_script_imports(__file__)

try:
    from silicon_flow_embeddings import SiliconFlowEmbeddings
except ImportError:
    print("⚠️  Warning: silicon_flow_embeddings module not found")
    SiliconFlowEmbeddings = None

try:
    from embedding_config import resolve_embedding_config
except ImportError:
    print("⚠️  Warning: embedding_config module not found")
    resolve_embedding_config = None

try:
    from langchain_community.vectorstores import Chroma
except ImportError:
    print("⚠️  Warning: langchain_community module not found")
    Chroma = None


class SimilarTargetsSearcher:
    def __init__(self, similarity_threshold: float = 0.3, top_k: int = 50, chroma_dir: str = None):
        self.results = []
        self.similarity_threshold = similarity_threshold
        self.top_k = top_k
        
        if resolve_embedding_config is None:
            raise ValueError("embedding_config helper is required")

        try:
            embedding_config = resolve_embedding_config(__file__)
        except KeyError as exc:
            missing_key = exc.args[0]
            raise ValueError(
                f"{missing_key} environment variable is required for stage3 retrieval. "
                "Configure artifact/config/embedding.env when testing new hexshas."
            ) from exc
        
        if SiliconFlowEmbeddings is None or Chroma is None:
            print("❌ Required dependencies not available")
            self.vectorstore = None
            return
        
        self.embeddings = SiliconFlowEmbeddings(
            api_key=embedding_config["api_key"],
            model_name=embedding_config["model"],
            api_url=embedding_config["base_url"],
        )
        chroma_path = Path(chroma_dir) if chroma_dir else get_chroma_dir(__file__)
        

        try:
            self.vectorstore = Chroma(
                persist_directory=str(chroma_path),
                embedding_function=self.embeddings,
                collection_name="kernel_api_docs"
            )
            print(f"✅ Vectorstore loaded successfully from {chroma_path}")
            
            # Check if vectorstore contains any data
            try:
                collection = self.vectorstore._collection
                count = collection.count()
                print(f"  📊 Collection count: {count}")
            except Exception as count_error:
                print(f"  ⚠️  Could not get collection count: {count_error}")
                
        except Exception as e:
            print(f"❌ Failed to load vectorstore: {e}")
            self.vectorstore = None

    def search_similar_targets(self, generalized_target: str, top_k: int = None, similarity_threshold: float = None) -> dict:
        """
        Search for similar targets using generalized target description
        Args:
            generalized_target: The target description to search for
            top_k: Maximum number of results to retrieve (uses instance default if None)
            similarity_threshold: Minimum similarity score (uses instance default if None)
        Returns: dict with similar_target_count, similar_target_list, target_descriptions
        """
        # Use instance defaults if not provided
        if top_k is None:
            top_k = self.top_k
        if similarity_threshold is None:
            similarity_threshold = self.similarity_threshold
            
        print(f"🔍 Searching for targets similar to: {generalized_target}")
        print(f"  🎯 Using top_k: {top_k}, similarity threshold: {similarity_threshold} (keeping scores >= {similarity_threshold})")
        
        if self.vectorstore is None:
            print("  ❌ RAG not available, cannot proceed")
            return {
                "similar_target_count": 0,
                "similar_target_list": [],
                "target_descriptions": {},
                "similarity_scores": {}
            }
        
        try:
            # Search with RAG using the generalized target as query
            search_query = f"functions that {generalized_target.lower()}"
            retrieved_docs = self.vectorstore.similarity_search_with_score(search_query, k=top_k)
            print(f"  📋 Found {len(retrieved_docs)} relevant documents from RAG")
            
            similar_targets = []
            target_descriptions = {}
            seen_functions = set()
            filtered_count = 0
            
            for doc, score in retrieved_docs:
                func_name = doc.metadata.get('function_name', 'unknown')
                if func_name != 'unknown' and func_name not in seen_functions:
                    similarity_score = 1.0 - score  # Convert distance to similarity
                    
                    if similarity_score >= similarity_threshold:
                        seen_functions.add(func_name)
                        
                        similar_targets.append({
                            "function_name": func_name,
                            "similarity_score": float(similarity_score)
                        })
                        
                        description = self._extract_function_description(doc.page_content)
                        target_descriptions[func_name] = description
                    else:
                        filtered_count += 1
            
            similar_targets.sort(key=lambda x: x['similarity_score'], reverse=True)
            
            result = {
                "similar_target_count": len(similar_targets),
                "similar_target_list": [item["function_name"] for item in similar_targets],
                "target_descriptions": target_descriptions,
                "similarity_scores": {item["function_name"]: item["similarity_score"] for item in similar_targets}
            }
            
            print(f"  ✅ Found {len(similar_targets)} similar targets (filtered out {filtered_count} below threshold)")
            if similar_targets:
                print(f"  📊 Top 3 matches:")
                for i, target in enumerate(similar_targets[:3], 1):
                    print(f"    {i}. {target['function_name']}: {target['similarity_score']:.3f}")
            elif filtered_count > 0:
                print(f"  ⚠️  All {filtered_count} results were below similarity threshold {similarity_threshold}")
            
            return result
            
        except Exception as e:
            print(f"  ❌ Search failed: {e}")
            return {
                "similar_target_count": 0,
                "similar_target_list": [],
                "target_descriptions": {},
                "similarity_scores": {}
            }

    def _extract_function_description(self, content: str) -> str:
        lines = content.split('\n')
        description_lines = []
        
        in_description = False
        for line in lines:
            line = line.strip()
            if not line:
                continue
                
            if line.startswith('#') or line.startswith('Name:') or line.startswith('Function:'):
                continue
                
            if any(keyword in line.lower() for keyword in ['description:', 'summary:', 'purpose:']):
                in_description = True
                # Extract the description part after the colon
                if ':' in line:
                    desc_part = line.split(':', 1)[1].strip()
                    if desc_part:
                        description_lines.append(desc_part)
                continue
                
            # If we're in description section, collect lines
            if in_description:
                if line.endswith(':') or line.startswith('Parameters:') or line.startswith('Return:'):
                    break
                description_lines.append(line)
        
        # If no structured description found, use first few meaningful lines
        if not description_lines:
            meaningful_lines = [line.strip() for line in lines if line.strip() and 
                              not line.strip().startswith('#') and 
                              not any(x in line for x in ['Name:', 'Function:', '===', '---'])]
            description_lines = meaningful_lines[:2]  # Take first 2 meaningful lines
        
        description = ' '.join(description_lines).strip()
        if len(description) > 200:
            description = description[:197] + "..."
            
        return description if description else "No description available"

    @staticmethod
    def _validate_stage2_csv_schema(df: pd.DataFrame) -> None:
        required_columns = {
            "hexsha",
            "generalized_target",
            "generalized_predicate",
            "generalization_status",
        }
        missing_columns = sorted(required_columns - set(df.columns))
        if missing_columns:
            raise ValueError(f"Invalid stage2 CSV schema, missing columns: {missing_columns}")

    @staticmethod
    def _validate_stage2_row(row_data: dict) -> None:
        hexsha = str(row_data.get("hexsha", "") or "")
        status = str(row_data.get("generalization_status", "") or "").strip()
        generalized_target = str(row_data.get("generalized_target", "") or "").strip()
        generalized_predicate = str(row_data.get("generalized_predicate", "") or "").strip()

        if status == "completed" and (not generalized_target or not generalized_predicate):
            raise ValueError(
                f"Invalid stage2 output for {hexsha[:12]}: "
                "completed row missing generalized_target/generalized_predicate"
            )

    def process_single_row(self, row_data: dict) -> dict:
        hexsha = row_data['hexsha']
        self._validate_stage2_row(row_data)
        generalized_target = row_data.get('generalized_target', '')
        
        print(f"Processing {hexsha[:12]}...")
        
        result = row_data.copy()
        
        if not generalized_target or str(generalized_target).strip() in ['', 'nan']:
            print(f"  ⚠️  Skipping {hexsha[:12]} - no generalized target")
            result.update({
                "similar_target_count": 0,
                "similar_target_list": "[]",
                "target_descriptions": "{}",
                "similarity_scores": "{}"
            })
            return result
        
        search_result = self.search_similar_targets(generalized_target)
        
        result.update({
            "similar_target_count": search_result["similar_target_count"],
            "similar_target_list": json.dumps(search_result["similar_target_list"]),
            "target_descriptions": json.dumps(search_result["target_descriptions"]),
            "similarity_scores": json.dumps(search_result["similarity_scores"])
        })
        
        return result

    def _generate_output_filename(self, input_csv, threshold=None, top_k=None):
        """Generate output filename based on input filename pattern and parameters"""
        # Use instance defaults if not provided
        if threshold is None:
            threshold = self.similarity_threshold
        if top_k is None:
            top_k = self.top_k
            
        match = re.search(r'step2_specifcation_generalization_(\d{8}_\d{6})\.csv$', input_csv)
        
        # Format threshold for filename (keep two decimal places, replace . with underscore)
        threshold_str = f"{threshold:.2f}".replace('.', '_')
        
        if match:
            timestamp_suffix = match.group(1)
            return f"step3_similar_target_search_{timestamp_suffix}_threshold_{threshold_str}_topk_{top_k}.csv"
        else:
            # Fallback to default behavior if pattern doesn't match
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            return f"step3_similar_target_search_{timestamp}_threshold_{threshold_str}_topk_{top_k}.csv"
    
    def _generate_json_filename(self, csv_filename):
        return csv_filename.replace('.csv', '.json')

    def process_csv(self, input_csv, output_csv=None):
        """Process all rows from the generalization CSV file with resume capability"""
        print(f"Reading generalization results from: {input_csv}")
        
        final_csv = output_csv if output_csv else self._generate_output_filename(input_csv, self.similarity_threshold, self.top_k)
        
        # Check for existing partial results with matching parameters
        existing_results = {}
        if os.path.exists(final_csv):
            print(f"📁 Found existing results file: {final_csv}")
            try:
                existing_df = pd.read_csv(final_csv)
                existing_results = {row['hexsha']: row for _, row in existing_df.iterrows()}
                print(f"📋 Loaded {len(existing_results)} existing results")
                print(f"✅ Parameters match - using existing results for resume")
            except Exception as e:
                print(f"⚠️  Warning: Could not load existing results: {e}")
        else:
            print(f"📄 Will create new results file: {final_csv}")
        
        df = pd.read_csv(input_csv, comment='#')
        self._validate_stage2_csv_schema(df)
        rows = df.to_dict('records')
        
        pending_rows = [r for r in rows if r['hexsha'] not in existing_results]
        
        print(f"📊 Total rows: {len(rows)}, Already processed: {len(existing_results)}, Pending: {len(pending_rows)}")
        print(f"🎯 Using parameters: threshold={self.similarity_threshold}, top_k={self.top_k}")
        
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
                
                if i % 5 == 0:
                    self._save_progress(final_csv)
                    print(f"💾 Progress saved ({i}/{len(pending_rows)} processed) - CSV & JSON")
        
        except KeyboardInterrupt:
            print("\n⚠️  Process interrupted by user")
            self._save_progress(final_csv)
            print(f"💾 Progress saved to:")
            print(f"   📄 CSV: {final_csv}")
            print(f"   📋 JSON: {final_csv.replace('.csv', '.json')}")
            print("🔄 You can resume by running the same command again")
            return
        except Exception as e:
            print(f"\n❌ Error occurred: {e}")
            self._save_progress(final_csv)
            print(f"💾 Progress saved to:")
            print(f"   📄 CSV: {final_csv}")
            print(f"   📋 JSON: {final_csv.replace('.csv', '.json')}")
            raise
        
        self._save_progress(final_csv)
        
        total = len(self.results)
        successful = sum(1 for r in self.results if r.get('similar_target_count', 0) > 0)
        
        print(f"\n✅ Similar target search complete!")
        print(f"📊 Summary: {successful} found similar targets, {total - successful} no matches / {total} total")
        print(f"💾 Results saved to:")
        print(f"   📄 CSV: {final_csv}")
        print(f"   📋 JSON: {final_csv.replace('.csv', '.json')}")
    
    def _save_progress(self, output_file):
        try:
            results_df = pd.DataFrame(self.results)
            results_df.to_csv(output_file, index=False)
            
            json_file = output_file.replace('.csv', '.json')
            self._save_json_results(json_file)
            
        except Exception as e:
            print(f"⚠️  Failed to save progress: {e}")
    
    def _save_json_results(self, json_file):
        try:
            json_results = []
            for result in self.results:
                json_result = result.copy()
                
                # Parse JSON strings back to objects for cleaner JSON output
                if 'similar_target_list' in json_result:
                    try:
                        json_result['similar_target_list'] = json.loads(json_result['similar_target_list'])
                    except (json.JSONDecodeError, TypeError):
                        # Keep as string if parsing fails
                        pass
                
                if 'target_descriptions' in json_result:
                    try:
                        json_result['target_descriptions'] = json.loads(json_result['target_descriptions'])
                    except (json.JSONDecodeError, TypeError):
                        # Keep as string if parsing fails
                        pass
                
                if 'similarity_scores' in json_result:
                    try:
                        json_result['similarity_scores'] = json.loads(json_result['similarity_scores'])
                    except (json.JSONDecodeError, TypeError):
                        # Keep as string if parsing fails
                        pass
                
                json_results.append(json_result)
            
            with open(json_file, 'w', encoding='utf-8') as f:
                json.dump(json_results, f, indent=2, ensure_ascii=False)
            
            print(f"💾 JSON results saved to: {json_file}")
            
        except Exception as e:
            print(f"⚠️  Failed to save JSON results: {e}")


def main():
    parser = argparse.ArgumentParser(description='Similar Target Search Tool')
    parser.add_argument('input_csv', nargs='?', help='Input CSV file with generalization results')
    parser.add_argument('--output', default=None,
                       help='Output CSV file')
    parser.add_argument('--test', help='Test single generalized target directly')
    parser.add_argument('--threshold', type=float, default=0.35,
                       help='Similarity threshold (0.0-1.0), default: 0.4')
    parser.add_argument('--top-k', type=int, default=100,
                       help='Maximum number of results to retrieve, default: 50')
    parser.add_argument('--chroma-dir', default=None,
                       help='Path to the prebuilt Chroma store')
    
    args = parser.parse_args()
    
    searcher = SimilarTargetsSearcher(
        similarity_threshold=args.threshold,
        top_k=args.top_k,
        chroma_dir=args.chroma_dir,
    )
    
    # Test mode: search for single generalized target
    if args.test:
        print(f"Testing single generalized target: {args.test}")
        result = searcher.search_similar_targets(args.test)
        print("\n" + "="*50)
        print("📊 Search Result:")
        print("="*50)
        print(f"Similar target count: {result['similar_target_count']}")
        print(f"Similar target list: {result['similar_target_list'][:5]}...")  # Show first 5
        print("Target descriptions and scores (first 3):")
        for i, func in enumerate(result['similar_target_list'][:3], 1):
            desc = result['target_descriptions'].get(func, 'No description')
            score = result['similarity_scores'].get(func, 0.0)
            print(f"  {i}. {func} (score: {score:.3f}): {desc}")
        return
    
    if not args.input_csv:
        parser.error("input_csv is required when not using --test mode")
    
    searcher.process_csv(args.input_csv, args.output)


if __name__ == '__main__':
    main()
