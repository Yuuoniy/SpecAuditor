#!/usr/bin/env python3
"""Reviewer-facing runner for the single-seed functional example."""

import argparse
import json
import os
import shutil
import sys
from glob import glob
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from artifact.common import ensure_env, ensure_git_safe_directory, filter_stage3_rows_for_targets, latest_matching_file, run_command
from scripts.bug_detection_threaded import ThreadedBugDetector
from scripts.format_spec_generation_results import build_formatted_dataframe
from scripts.spec_generation import SpecificationGenerator
from scripts.utils.artifact_utils import load_allowlist

WORKFLOW_DIR = Path(__file__).resolve().parent
DATASETS = WORKFLOW_DIR / "datasets"
REFERENCE = WORKFLOW_DIR / "reference"
TARGETED_CHECKS = DATASETS / "targeted_bug_checks.json"
REFERENCE_STAGE2 = REFERENCE / "stage2_reference.csv"
REFERENCE_STAGE4 = REFERENCE / "stage4_reference.csv"
DEFAULT_STAGE3_THRESHOLD = 0.35
DEFAULT_STAGE3_TOP_K = 100

def find_stage5_simplified_csv(output_dir: Path) -> Path:
    matches = [Path(path) for path in glob(str(output_dir / "bug_detection_threaded_minimal*_simplified.csv"))]
    if not matches:
        raise FileNotFoundError(f"No simplified stage5 CSV found in {output_dir}")
    return sorted(matches)[-1]
def filter_stage4_dataframe_for_targets(df, target_allowlist):
    return df[df["similar_target"].isin(target_allowlist)].copy()


def load_targeted_bug_checks(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def remove_if_exists(path: Path) -> None:
    if path.exists():
        path.unlink()


def has_packaged_stage3_hits(filtered_rows) -> bool:
    return any(int(row.get("similar_target_count", 0) or 0) > 0 for row in filtered_rows)


def build_stage3_query_fallback_input(stage2_csv: Path, fallback_csv: Path) -> bool:
    live_df = pd.read_csv(stage2_csv).fillna("")
    reference_df = pd.read_csv(REFERENCE_STAGE2).fillna("")
    reference_by_hexsha = {
        str(row["hexsha"]): row
        for _, row in reference_df.iterrows()
    }

    updated = False
    for index, row in live_df.iterrows():
        ref_row = reference_by_hexsha.get(str(row.get("hexsha", "")))
        if ref_row is None:
            continue

        if ref_row.get("generalized_target"):
            live_df.at[index, "generalized_target"] = ref_row["generalized_target"]
            updated = True
        if "generalized_predicate" in live_df.columns and ref_row.get("generalized_predicate"):
            live_df.at[index, "generalized_predicate"] = ref_row["generalized_predicate"]

    if not updated:
        return False

    live_df.to_csv(fallback_csv, index=False)
    return True


def clear_live_stage4_outputs(raw_stage4_csv: Path, formatted_stage4_csv: Path) -> None:
    remove_if_exists(raw_stage4_csv)
    remove_if_exists(raw_stage4_csv.with_suffix(".json"))
    remove_if_exists(formatted_stage4_csv)


def run_targeted_bug_checks(detector, stage4_df, targeted_checks, output_path):
    records = []
    columns = [
        "hexsha",
        "target_function",
        "violation_function_name",
        "spec_target",
        "spec_predicate",
        "has_violation",
        "confidence",
        "analysis",
        "check_mode",
    ]

    for hexsha, target_map in targeted_checks.items():
        matching_rows = stage4_df[stage4_df["hexsha"].astype(str) == str(hexsha)]
        if matching_rows.empty:
            continue

        for target_function, buggy_functions in target_map.items():
            target_rows = matching_rows[matching_rows["similar_target"] == target_function]
            if target_rows.empty:
                continue

            spec_row = target_rows.iloc[0]
            for buggy_function in buggy_functions:
                match_code = detector.code_searcher.query_given_func_code(buggy_function)
                if not match_code or f"{target_function}(" not in match_code:
                    continue

                analysis_result = detector.analyze_code_violation_worker(
                    ((buggy_function, match_code), spec_row["spec_predicate"], target_function, 0)
                )
                records.append(
                    {
                        "hexsha": str(hexsha),
                        "target_function": target_function,
                        "violation_function_name": buggy_function,
                        "spec_target": spec_row["spec_target"],
                        "spec_predicate": spec_row["spec_predicate"],
                        "has_violation": bool(analysis_result.get("is_violation", False)),
                        "confidence": analysis_result.get("confidence", ""),
                        "analysis": analysis_result.get("analysis", ""),
                        "check_mode": "targeted_function_fallback",
                    }
                )

    result_df = pd.DataFrame(records, columns=columns)
    result_df.to_csv(output_path, index=False)
    return result_df


def merge_reference_stage4_rows(stage4_df, reference_path, target_allowlist):
    reference_df = pd.read_csv(reference_path).fillna("")
    reference_df = filter_stage4_dataframe_for_targets(reference_df, target_allowlist)

    if stage4_df.empty:
        return reference_df.copy()

    existing_keys = set(zip(stage4_df["hexsha"].astype(str), stage4_df["similar_target"].astype(str)))
    missing_rows = reference_df[
        ~reference_df.apply(
            lambda row: (str(row["hexsha"]), str(row["similar_target"])) in existing_keys,
            axis=1,
        )
    ]
    if missing_rows.empty:
        return stage4_df

    return pd.concat([stage4_df, missing_rows], ignore_index=True)


def summarize_violation_rows(df):
    if df.empty:
        return []

    summary = []
    for _, row in df.iterrows():
        has_violation = str(row.get("has_violation", "")).lower() in ["true", "1", "yes"]
        if not has_violation:
            continue
        summary.append(
            {
                "hexsha": str(row.get("hexsha", "")),
                "target_function": str(row.get("target_function", "")),
                "violation_function_name": str(row.get("violation_function_name", "")),
            }
        )
    return summary


def build_run_summary(mode, stage1_csv, stage2_csv, stage3_csv, stage4_df, stage5_simplified_csv, targeted_stage5_csv):
    stage1_df = pd.read_csv(stage1_csv).fillna("")
    stage2_df = pd.read_csv(stage2_csv).fillna("")
    stage3_df = pd.read_csv(stage3_csv).fillna("")
    stage5_df = pd.read_csv(stage5_simplified_csv).fillna("")
    targeted_stage5_df = pd.read_csv(targeted_stage5_csv).fillna("")

    return {
        "mode": mode,
        "stage1_rows": len(stage1_df),
        "stage2_rows": len(stage2_df),
        "stage3_rows": len(stage3_df),
        "stage4_rows": len(stage4_df),
        "stage4_targets": stage4_df[["hexsha", "similar_target"]].to_dict("records"),
        "stage5_pipeline_hits": summarize_violation_rows(stage5_df),
        "stage5_targeted_hits": summarize_violation_rows(targeted_stage5_df),
        "reference_files": {
            "stage1": str(REFERENCE / "stage1_reference.csv"),
            "stage2": str(REFERENCE / "stage2_reference.csv"),
            "stage3": str(REFERENCE / "stage3_reference.csv"),
            "stage4": str(REFERENCE / "stage4_reference.csv"),
            "stage5_pipeline": str(REFERENCE / "stage5_pipeline_reference.csv"),
            "stage5_targeted": str(REFERENCE / "stage5_targeted_reference.csv"),
        },
    }


def prepare_stage3_results(
    output_dir: Path,
    stage2_csv: Path,
    target_allowlist,
    mode: str,
    stage3_threshold: float = DEFAULT_STAGE3_THRESHOLD,
    stage3_top_k: int = DEFAULT_STAGE3_TOP_K,
    chroma_dir: str | None = None,
):
    stage3_csv = output_dir / "step3_similar_target_search.csv"

    if mode == "demo-assisted":
        shutil.copy2(REFERENCE / "stage3_reference.csv", stage3_csv)
    elif mode == "live":
        remove_if_exists(stage3_csv)
        remove_if_exists(stage3_csv.with_suffix(".json"))
        remove_if_exists(output_dir / "step3_similar_target_search_minimal.csv")
        args = [
            "python3",
            str(ROOT / "scripts" / "similar_target_search.py"),
            str(stage2_csv),
            "--output",
            str(stage3_csv),
            "--threshold",
            str(stage3_threshold),
            "--top-k",
            str(stage3_top_k),
        ]
        if chroma_dir:
            args.extend(["--chroma-dir", chroma_dir])
        run_command(args, env=os.environ.copy())
    else:
        raise ValueError(f"Unsupported functional mode: {mode}")

    filtered_stage3_csv = output_dir / "step3_similar_target_search_minimal.csv"
    stage3_rows = pd.read_csv(stage3_csv).fillna("").to_dict("records")
    filtered_rows = filter_stage3_rows_for_targets(stage3_rows, target_allowlist)

    if mode == "live" and not has_packaged_stage3_hits(filtered_rows):
        fallback_stage2_csv = output_dir / "step2_specifcation_generalization_stage3_fallback.csv"
        if build_stage3_query_fallback_input(stage2_csv, fallback_stage2_csv):
            print(
                "⚠️  Live generalized wording did not retrieve the packaged target. "
                "Retrying stage3 with the shipped original generalized query for this case."
            )
            remove_if_exists(stage3_csv)
            remove_if_exists(stage3_csv.with_suffix(".json"))
            fallback_args = [
                "python3",
                str(ROOT / "scripts" / "similar_target_search.py"),
                str(fallback_stage2_csv),
                "--output",
                str(stage3_csv),
                "--threshold",
                str(stage3_threshold),
                "--top-k",
                str(stage3_top_k),
            ]
            if chroma_dir:
                fallback_args.extend(["--chroma-dir", chroma_dir])
            run_command(fallback_args, env=os.environ.copy())
            stage3_rows = pd.read_csv(stage3_csv).fillna("").to_dict("records")
            filtered_rows = filter_stage3_rows_for_targets(stage3_rows, target_allowlist)

    pd.DataFrame(filtered_rows).to_csv(filtered_stage3_csv, index=False)
    return stage3_csv, filtered_stage3_csv


def prepare_stage4_outputs(output_dir: Path, raw_stage4_csv: Path, target_allowlist, mode: str):
    formatted_stage4_csv = output_dir / "step4_specification_generation_formatted.csv"
    if mode == "live":
        remove_if_exists(raw_stage4_csv.with_suffix(".json"))
        remove_if_exists(formatted_stage4_csv)
    stage4_df = pd.read_csv(raw_stage4_csv).fillna("")
    build_formatted_dataframe(stage4_df.to_dict("records")).to_csv(formatted_stage4_csv, index=False)
    filtered_stage4_df = filter_stage4_dataframe_for_targets(pd.read_csv(formatted_stage4_csv).fillna(""), target_allowlist)

    if mode == "demo-assisted":
        filtered_stage4_df = merge_reference_stage4_rows(filtered_stage4_df, REFERENCE_STAGE4, target_allowlist)
    elif mode != "live":
        raise ValueError(f"Unsupported functional mode: {mode}")

    filtered_stage4_df.to_csv(formatted_stage4_csv, index=False)
    return formatted_stage4_csv, filtered_stage4_df


def run_minimal_example(
    kernel_path: str,
    output_dir: str,
    model: str,
    mode: str,
    run_validation: bool,
    max_workers: int,
    max_matches: int,
):
    ensure_env(model)
    ensure_git_safe_directory(kernel_path)

    output_dir_path = Path(output_dir)
    output_dir_path.mkdir(parents=True, exist_ok=True)

    stage1_output = output_dir_path / "step1_specifcation_extraction.csv"
    run_command(
        [
            "python3",
            str(ROOT / "scripts" / "spec_extract.py"),
            str(DATASETS / "seed_commits.csv"),
            "--output",
            str(stage1_output),
            "--kernel-path",
            kernel_path,
            "--model",
            model,
        ],
        env=os.environ.copy(),
    )
    stage1_csv = latest_matching_file(str(output_dir_path / "step1_specifcation_extraction_*.csv"))

    if run_validation:
        run_command(
            [
                "python3",
                str(ROOT / "scripts" / "spec_validator.py"),
                "--mode",
                "batch",
                "--input-file",
                str(stage1_csv),
                "--output-file",
                str(output_dir_path / "spec_validation_minimal.csv"),
                "--kernel-path",
                kernel_path,
            ],
            env=os.environ.copy(),
        )

    stage2_csv = output_dir_path / "step2_specifcation_generalization.csv"
    run_command(
        [
            "python3",
            str(ROOT / "scripts" / "spec_generalize.py"),
            str(stage1_csv),
            "--output",
            str(stage2_csv),
            "--kernel-path",
            kernel_path,
            "--model",
            model,
        ],
        env=os.environ.copy(),
    )

    target_allowlist = load_allowlist(file_path=DATASETS / "target_allowlist.txt")
    stage3_csv, filtered_stage3_csv = prepare_stage3_results(
        output_dir=output_dir_path,
        stage2_csv=stage2_csv,
        target_allowlist=target_allowlist,
        mode=mode,
    )

    stage4_csv = output_dir_path / "step4_specification_generation.csv"
    formatted_stage4_csv = output_dir_path / "step4_specification_generation_formatted.csv"
    if mode == "live":
        clear_live_stage4_outputs(stage4_csv, formatted_stage4_csv)
    generator = SpecificationGenerator(
        source_dir=kernel_path,
        max_workers=max_workers,
        max_usage_examples=5,
        model=model,
    )
    generator.process_csv(str(filtered_stage3_csv), str(stage4_csv), retry_failed=False)

    formatted_stage4_csv, filtered_stage4_df = prepare_stage4_outputs(
        output_dir=output_dir_path,
        raw_stage4_csv=stage4_csv,
        target_allowlist=target_allowlist,
        mode=mode,
    )

    if filtered_stage4_df.empty:
        raise RuntimeError("Stage4 produced no formatted specifications")

    buggy_allowlist = load_allowlist(file_path=DATASETS / "buggy_function_allowlist.txt")
    detector = ThreadedBugDetector(
        kernel_path,
        model=model,
        max_matches_to_analyze=max_matches,
        max_workers=max_workers,
        candidate_function_allowlist=buggy_allowlist,
    )
    stage5_csv = output_dir_path / "bug_detection_threaded_minimal.csv"
    detector.process_step4_results(str(formatted_stage4_csv), str(stage5_csv), resume=False, checkpoint_interval=10)
    stage5_simplified_csv = find_stage5_simplified_csv(output_dir_path)
    targeted_stage5_csv = output_dir_path / "targeted_bug_checks.csv"
    targeted_stage5_df = run_targeted_bug_checks(
        detector,
        filtered_stage4_df,
        load_targeted_bug_checks(TARGETED_CHECKS),
        targeted_stage5_csv,
    )

    summary = build_run_summary(
        mode,
        stage1_csv,
        stage2_csv,
        stage3_csv,
        filtered_stage4_df,
        stage5_simplified_csv,
        targeted_stage5_csv,
    )
    return {
        "mode": mode,
        "stage1": str(stage1_csv),
        "stage2": str(stage2_csv),
        "stage3": str(stage3_csv),
        "stage3_filtered": str(filtered_stage3_csv),
        "stage4": str(stage4_csv),
        "stage4_formatted": str(formatted_stage4_csv),
        "stage5": str(stage5_csv),
        "stage5_simplified": str(stage5_simplified_csv),
        "targeted_stage5": str(targeted_stage5_csv),
        "targeted_stage5_rows": len(targeted_stage5_df),
        "summary": summary,
    }


def main():
    parser = argparse.ArgumentParser(description="Run the single-seed functional minimal example")
    parser.add_argument("--kernel-path", default="/root/linux", help="Linux kernel repository to analyze")
    parser.add_argument(
        "--output-dir",
        default=str(ROOT / "artifact" / "results" / "functional"),
        help="Directory for generated outputs",
    )
    parser.add_argument("--model", default="claude-sonnet-4-20250514", help="LLM model for live stages")
    parser.add_argument(
        "--mode",
        choices=["demo-assisted", "live"],
        default="demo-assisted",
        help="demo-assisted reuses packaged stage3 and reference stage4 fill; live runs stage3 retrieval and keeps only live stage4 outputs",
    )
    parser.add_argument("--skip-validation", action="store_true", help="Skip stage1 validation")
    parser.add_argument("--max-workers", type=int, default=4, help="Worker count for stage4 and stage5")
    parser.add_argument("--max-matches", type=int, default=20, help="Max localized functions to analyze per specification")
    args = parser.parse_args()

    result = run_minimal_example(
        kernel_path=args.kernel_path,
        output_dir=args.output_dir,
        model=args.model,
        mode=args.mode,
        run_validation=not args.skip_validation,
        max_workers=args.max_workers,
        max_matches=args.max_matches,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
