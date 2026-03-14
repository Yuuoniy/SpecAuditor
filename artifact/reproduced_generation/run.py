#!/usr/bin/env python3
"""Reviewer-facing reproduced runner for specification generation."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from artifact.common import (
        build_stage4_summary,
        ensure_env,
        ensure_git_safe_directory,
        filter_stage3_rows_for_targets,
        latest_matching_file,
        run_command,
        write_summary,
    )
except ImportError:
    from common import (  # type: ignore
        build_stage4_summary,
        ensure_env,
        ensure_git_safe_directory,
        filter_stage3_rows_for_targets,
        latest_matching_file,
        run_command,
        write_summary,
    )
from scripts.format_spec_generation_results import build_formatted_dataframe
from scripts.utils.artifact_utils import load_allowlist

WORKFLOW_DIR = Path(__file__).resolve().parent
DATASETS = WORKFLOW_DIR / "datasets"
REFERENCE = WORKFLOW_DIR / "reference"


def load_stage4_target_allowlist() -> list[str]:
    df = pd.read_csv(DATASETS / "stage4_target_subset.csv").fillna("")
    return df["similar_target"].astype(str).str.strip().tolist()


def run_reproduced_spec_generation(kernel_path: str, output_dir: str, model: str, max_workers: int):
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

    stage3_csv = output_dir_path / "step3_similar_target_search.csv"
    shutil.copy2(REFERENCE / "stage3_reference.csv", stage3_csv)

    target_allowlist = load_allowlist(file_path=DATASETS / "stage4_target_subset.txt")
    filtered_stage3_csv = output_dir_path / "step3_similar_target_search_reproduced.csv"
    stage3_rows = pd.read_csv(stage3_csv).to_dict("records")
    pd.DataFrame(filter_stage3_rows_for_targets(stage3_rows, target_allowlist)).to_csv(filtered_stage3_csv, index=False)

    stage4_csv = output_dir_path / "step4_specification_generation.csv"
    run_command(
        [
            "python3",
            str(ROOT / "scripts" / "spec_generation.py"),
            str(filtered_stage3_csv),
            "--output",
            str(stage4_csv),
            "--source-dir",
            kernel_path,
            "--max-workers",
            str(max_workers),
            "--model",
            model,
        ],
        env=os.environ.copy(),
    )

    formatted_stage4_csv = output_dir_path / "step4_specification_generation_formatted.csv"
    stage4_df = pd.read_csv(stage4_csv).fillna("")
    formatted_stage4_df = build_formatted_dataframe(stage4_df.to_dict("records"))
    formatted_stage4_df.to_csv(formatted_stage4_csv, index=False)

    summary = {
        "seed_count": int(pd.read_csv(DATASETS / "seed_commits.csv").shape[0]),
        "quick_generation_target_budget": len(load_stage4_target_allowlist()),
        "reference_files": {
            "seed_reference": str(REFERENCE / "seed_reference.csv"),
            "stage3_reference": str(REFERENCE / "stage3_reference.csv"),
            "stage4_reference_subset": str(REFERENCE / "stage4_reference_subset.csv"),
            "stage4_reference_full": str(REFERENCE / "stage4_reference_full.csv"),
            "summary": str(REFERENCE / "summary.json"),
        },
    }
    summary.update(build_stage4_summary(formatted_stage4_df))

    per_seed_summary_csv = output_dir_path / "reproduced_spec_generation_summary.csv"
    if not formatted_stage4_df.empty:
        (
            formatted_stage4_df.fillna("")
            .groupby("hexsha")
            .agg(
                stage4_rows=("similar_target", "size"),
                unique_targets=("similar_target", "nunique"),
                unique_specs=("spec_target", "nunique"),
            )
            .reset_index()
            .to_csv(per_seed_summary_csv, index=False)
        )
    else:
        pd.DataFrame(columns=["hexsha", "stage4_rows", "unique_targets", "unique_specs"]).to_csv(
            per_seed_summary_csv, index=False
        )

    summary["summary_csv"] = str(per_seed_summary_csv)
    summary_path = output_dir_path / "reproduced_spec_generation_summary.json"
    write_summary(summary_path, summary)

    return {
        "stage1": str(stage1_csv),
        "stage2": str(stage2_csv),
        "stage3": str(stage3_csv),
        "stage3_filtered": str(filtered_stage3_csv),
        "stage4": str(stage4_csv),
        "stage4_formatted": str(formatted_stage4_csv),
        "summary_json": str(summary_path),
        "summary_csv": str(per_seed_summary_csv),
        "summary": summary,
    }


def main():
    parser = argparse.ArgumentParser(description="Run the reproduced subset specification-generation evaluation")
    parser.add_argument("--kernel-path", default="/root/linux", help="Linux kernel repository to analyze")
    parser.add_argument(
        "--output-dir",
        default=str(ROOT / "artifact" / "results" / "reproduced_generation"),
        help="Directory for generated outputs",
    )
    parser.add_argument("--model", default="claude-sonnet-4-20250514", help="LLM model for live stages")
    parser.add_argument("--max-workers", type=int, default=4, help="Worker count for stage4 generation")
    args = parser.parse_args()

    result = run_reproduced_spec_generation(
        kernel_path=args.kernel_path,
        output_dir=args.output_dir,
        model=args.model,
        max_workers=args.max_workers,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
