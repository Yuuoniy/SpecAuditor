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


def display_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


def benchmark_row_key(row_dict: dict) -> tuple[str, str, str]:
    return (
        str(row_dict.get("seed patch", "")).strip(),
        str(row_dict.get("detected target", "")).strip(),
        str(row_dict.get("buggy function", "")).strip(),
    )


def spec_run_key(row_dict: dict) -> tuple[str, str, str, str]:
    return (
        str(row_dict.get("seed patch", "")).strip(),
        str(row_dict.get("detected target", "")).strip(),
        str(row_dict.get("spec_target", "")).strip(),
        str(row_dict.get("spec_predicate", "")).strip(),
    )


def build_audit_candidate_set(
    candidates: dict,
    expected_buggy_function: str,
    max_candidates_to_audit: int,
) -> tuple[list[tuple[str, str]], dict]:
    ordered_candidates = list(candidates.items())
    selected_candidates = ordered_candidates[:max_candidates_to_audit]

    expected_function_found = expected_buggy_function in candidates
    expected_function_in_default_budget = False
    expected_function_forced_into_audit_set = False

    if expected_function_found:
        expected_function_in_default_budget = any(
            candidate_name == expected_buggy_function
            for candidate_name, _ in selected_candidates
        )

        if (
            max_candidates_to_audit > 0
            and not expected_function_in_default_budget
            and selected_candidates
        ):
            expected_entry = (expected_buggy_function, candidates[expected_buggy_function])
            selected_candidates = selected_candidates[: max_candidates_to_audit - 1] + [expected_entry]
            expected_function_forced_into_audit_set = True

    metadata = {
        "candidate_count": len(ordered_candidates),
        "expected_function_found": expected_function_found,
        "expected_function_in_default_budget": expected_function_in_default_budget,
        "expected_function_forced_into_audit_set": expected_function_forced_into_audit_set,
        "audited_candidate_count": len(selected_candidates),
    }
    return selected_candidates, metadata


def build_group_audit_candidate_set(
    candidates: dict,
    expected_buggy_functions: list[str],
    max_candidates_to_audit: int,
) -> tuple[list[tuple[str, str]], dict[str, dict]]:
    ordered_candidates = list(candidates.items())
    default_selected_candidates = ordered_candidates[:max_candidates_to_audit]
    default_selected_names = [candidate_name for candidate_name, _ in default_selected_candidates]
    selected_names = list(default_selected_names)

    found_expected_functions = [
        expected_buggy_function
        for expected_buggy_function in expected_buggy_functions
        if expected_buggy_function in candidates
    ]

    if max_candidates_to_audit > 0:
        for expected_buggy_function in found_expected_functions:
            if expected_buggy_function in selected_names:
                continue
            replace_idx = next(
                (
                    idx
                    for idx in range(len(selected_names) - 1, -1, -1)
                    if selected_names[idx] not in found_expected_functions
                ),
                None,
            )
            if replace_idx is None:
                break
            selected_names[replace_idx] = expected_buggy_function

    selected_candidates = [(candidate_name, candidates[candidate_name]) for candidate_name in selected_names]
    selected_name_set = set(selected_names)
    default_selected_name_set = set(default_selected_names)
    metadata_by_buggy_function = {}

    for expected_buggy_function in expected_buggy_functions:
        expected_function_found = expected_buggy_function in candidates
        expected_function_in_default_budget = expected_buggy_function in default_selected_name_set
        expected_function_forced_into_audit_set = (
            expected_function_found
            and expected_buggy_function in selected_name_set
            and not expected_function_in_default_budget
        )
        metadata_by_buggy_function[expected_buggy_function] = {
            "candidate_count": len(ordered_candidates),
            "expected_function_found": expected_function_found,
            "expected_function_in_default_budget": expected_function_in_default_budget,
            "expected_function_forced_into_audit_set": expected_function_forced_into_audit_set,
            "audited_candidate_count": len(selected_candidates),
        }

    return selected_candidates, metadata_by_buggy_function


def evaluate_bug_check_targeted(detector: ThreadedBugDetector, row_dict: dict, thread_id: int) -> dict:
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


def evaluate_bug_check_localized(
    detector: ThreadedBugDetector,
    row_dict: dict,
    thread_id: int,
    max_candidates_to_audit: int,
) -> list[dict]:
    target_function = str(row_dict["detected target"]).strip()
    expected_buggy_function = str(row_dict["buggy function"]).strip()
    spec_target = str(row_dict["spec_target"]).strip()
    spec_predicate = str(row_dict["spec_predicate"]).strip()

    localization_result = detector.localize_candidates_for_spec_with_metadata(target_function, spec_target)
    matching_functions = localization_result["matching_functions"]
    generated_query = localization_result["generated_query"]
    localization_error = localization_result["localization_error"]
    selected_candidates, metadata = build_audit_candidate_set(
        matching_functions,
        expected_buggy_function=expected_buggy_function,
        max_candidates_to_audit=max_candidates_to_audit,
    )

    if not selected_candidates:
        return [
            {
                **row_dict,
                "_row_index": row_dict["_row_index"],
                **metadata,
                "generated_query": generated_query,
                "localization_error": localization_error,
                "audited_function": "",
                "is_expected_buggy_function": False,
                "has_violation": False,
                "confidence": "NO_MATCH",
                "analysis": (
                    "No localized candidates were found for this specification."
                    if not localization_error
                    else f"Candidate localization failed before search: {localization_error}."
                ),
            }
        ]

    records = []
    for idx, (candidate_name, candidate_code) in enumerate(selected_candidates):
        analysis_result = detector.analyze_code_violation_worker(
            ((candidate_name, candidate_code), spec_predicate, target_function, thread_id + idx)
        )
        records.append(
            {
                **row_dict,
                "_row_index": row_dict["_row_index"],
                **metadata,
                "generated_query": generated_query,
                "localization_error": localization_error,
                "audited_function": candidate_name,
                "is_expected_buggy_function": candidate_name == expected_buggy_function,
                "has_violation": bool(analysis_result.get("is_violation", False)),
                "confidence": analysis_result.get("confidence", ""),
                "analysis": analysis_result.get("analysis", ""),
            }
        )

    return records


def evaluate_bug_check_localized_group(
    detector: ThreadedBugDetector,
    group_rows: list[dict],
    thread_id: int,
    max_candidates_to_audit: int,
) -> dict:
    representative_row = group_rows[0]
    target_function = str(representative_row["detected target"]).strip()
    spec_target = str(representative_row["spec_target"]).strip()
    spec_predicate = str(representative_row["spec_predicate"]).strip()
    expected_buggy_functions = [str(row["buggy function"]).strip() for row in group_rows]

    localization_result = detector.localize_candidates_for_spec_with_metadata(target_function, spec_target)
    matching_functions = localization_result["matching_functions"]
    generated_query = localization_result["generated_query"]
    localization_error = localization_result["localization_error"]
    selected_candidates, metadata_by_buggy_function = build_group_audit_candidate_set(
        matching_functions,
        expected_buggy_functions=expected_buggy_functions,
        max_candidates_to_audit=max_candidates_to_audit,
    )

    audited_candidate_results = []
    if selected_candidates:
        for idx, (candidate_name, candidate_code) in enumerate(selected_candidates):
            analysis_result = detector.analyze_code_violation_worker(
                ((candidate_name, candidate_code), spec_predicate, target_function, thread_id + idx)
            )
            audited_candidate_results.append(
                {
                    "audited_function": candidate_name,
                    "has_violation": bool(analysis_result.get("is_violation", False)),
                    "confidence": analysis_result.get("confidence", ""),
                    "analysis": analysis_result.get("analysis", ""),
                }
            )
    else:
        audited_candidate_results.append(
            {
                "audited_function": "",
                "has_violation": False,
                "confidence": "NO_MATCH",
                "analysis": (
                    "No localized candidates were found for this specification."
                    if not localization_error
                    else f"Candidate localization failed before search: {localization_error}."
                ),
            }
        )

    projected_rows = []
    for row_dict in group_rows:
        row_metadata = metadata_by_buggy_function[str(row_dict["buggy function"]).strip()]
        for audited_result in audited_candidate_results:
            projected_rows.append(
                {
                    **row_dict,
                    **row_metadata,
                    "generated_query": generated_query,
                    "localization_error": localization_error,
                    "audited_function": audited_result["audited_function"],
                    "is_expected_buggy_function": audited_result["audited_function"] == str(row_dict["buggy function"]).strip(),
                    "has_violation": audited_result["has_violation"],
                    "confidence": audited_result["confidence"],
                    "analysis": audited_result["analysis"],
                }
            )

    return {
        "rows": projected_rows,
        "audited_candidates": audited_candidate_results,
        "candidate_count": len(matching_functions),
        "expected_buggy_functions": expected_buggy_functions,
        "bug_row_count": len(group_rows),
        "seed patch": representative_row["seed patch"],
        "detected target": representative_row["detected target"],
        "spec_target": spec_target,
        "spec_predicate": spec_predicate,
    }


def build_localized_bug_detection_summary(result_rows: list[dict], generation_seed_count: int | None = None) -> dict:
    benchmark_rows: dict[tuple[str, str, str], dict] = {}

    for row in result_rows:
        key = benchmark_row_key(row)
        summary_row = benchmark_rows.setdefault(
            key,
            {
                "seed patch": str(row.get("seed patch", "")).strip(),
                "expected_function_found": bool(row.get("expected_function_found", False)),
                "expected_function_in_default_budget": bool(row.get("expected_function_in_default_budget", False)),
                "expected_function_forced_into_audit_set": bool(row.get("expected_function_forced_into_audit_set", False)),
                "expected_bug_detected": False,
            },
        )
        if bool(row.get("is_expected_buggy_function", False)) and normalize_bool(row.get("has_violation", False)):
            summary_row["expected_bug_detected"] = True

    seed_patches = {value["seed patch"] for value in benchmark_rows.values()}
    summary = {
        "localized_expected_function_found_rows": sum(
            1 for value in benchmark_rows.values() if value["expected_function_found"]
        ),
        "localized_expected_function_missed_rows": sum(
            1 for value in benchmark_rows.values() if not value["expected_function_found"]
        ),
        "detected_bug_rows": sum(1 for value in benchmark_rows.values() if value["expected_bug_detected"]),
        "seed_patch_count": len(seed_patches),
    }
    if generation_seed_count is not None:
        summary["generation_seed_count"] = generation_seed_count
    return summary


def run_localization_probe(
    detector: ThreadedBugDetector,
    row_dicts: list[dict],
    output_dir_path: Path,
    max_workers: int,
) -> dict:
    records = []

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_index = {
            executor.submit(
                detector.localize_candidates_for_spec_with_metadata,
                str(row_dict["detected target"]).strip(),
                str(row_dict["spec_target"]).strip(),
            ): idx
            for idx, row_dict in enumerate(row_dicts)
        }

        for completed_idx, future in enumerate(as_completed(future_to_index), 1):
            row_idx = future_to_index[future]
            row_dict = row_dicts[row_idx]
            localization_result = future.result()
            candidates = localization_result["matching_functions"]
            generated_query = localization_result["generated_query"]
            localization_error = localization_result["localization_error"]
            candidate_names = list(candidates.keys())
            expected_buggy_function = str(row_dict["buggy function"]).strip()
            expected_function_found = expected_buggy_function in candidates
            probe_record = {
                **row_dict,
                "generated_query": generated_query,
                "localization_error": localization_error,
                "candidate_count": len(candidate_names),
                "expected_function_found": expected_function_found,
            }
            records.append(probe_record)
            print(
                f"[{completed_idx}/{len(row_dicts)}] "
                f"{row_dict['seed patch']} :: {row_dict['detected target']} :: {row_dict['buggy function']} "
                f"-> {'FOUND' if expected_function_found else 'MISS'} "
                f"(candidates={len(candidate_names)})"
            )

    result_df = pd.DataFrame(records).sort_values("_row_index").drop(columns=["_row_index"])
    result_csv = output_dir_path / "reproduced_bug_detection_localization_probe.csv"
    result_df.to_csv(result_csv, index=False)
    generation_seed_count = int(pd.read_csv(GENERATION_SEEDS).shape[0]) if GENERATION_SEEDS.exists() else None
    summary = {
        "benchmark_bug_rows": int(len(result_df)),
        "localized_bug_rows": int(sum(bool(v) for v in result_df["expected_function_found"].tolist())),
        "localized_missed_bug_rows": int(sum(not bool(v) for v in result_df["expected_function_found"].tolist())),
        "localization_query_failures": int(sum(bool(str(v).strip()) for v in result_df["localization_error"].tolist())),
        "seed_patch_count": int(result_df["seed patch"].nunique()),
        "reference_files": {
            "benchmark_dataset": display_path(DATASET),
        },
    }
    if generation_seed_count is not None:
        summary["generation_seed_count"] = generation_seed_count

    return {
        "mode": "probe",
        "result_csv": str(result_csv),
        "summary": summary,
    }


def run_reproduced_bug_detection(
    kernel_path: str,
    output_dir: str,
    model: str,
    max_workers: int,
    mode: str,
    max_candidates_to_audit: int,
):
    ensure_env(model)
    ensure_git_safe_directory(kernel_path)

    output_dir_path = Path(output_dir)
    output_dir_path.mkdir(parents=True, exist_ok=True)

    checks_df = pd.read_csv(DATASET).fillna("")
    for col in checks_df.columns:
        checks_df[col] = checks_df[col].astype(str).str.strip()

    detector_kwargs = {
        "kernel_path": kernel_path,
        "model": model,
        "max_matches_to_analyze": max_candidates_to_audit,
        "max_workers": max_workers,
    }
    if mode == "targeted":
        detector_kwargs["candidate_function_allowlist"] = sorted(checks_df["buggy function"].astype(str).unique().tolist())
    detector = ThreadedBugDetector(**detector_kwargs)

    print(f"Loaded {len(checks_df)} bug-check rows across {checks_df['seed patch'].nunique()} seed patches")
    print(f"Running reproduced bug detection in {mode} mode with {max_workers} workers")
    print(
        "Effective settings: "
        f"kernel_path={kernel_path}, "
        f"max_workers={max_workers}, "
        f"max_candidates_to_audit={max_candidates_to_audit}"
    )

    row_dicts = checks_df.reset_index().rename(columns={"index": "_row_index"}).to_dict("records")
    if mode == "probe":
        return run_localization_probe(
            detector=detector,
            row_dicts=row_dicts,
            output_dir_path=output_dir_path,
            max_workers=max_workers,
        )

    records = []
    localized_audit_records = []
    file_stem = (
        "reproduced_bug_detection_results"
        if mode == "targeted"
        else "reproduced_bug_detection_localized_results"
    )
    partial_csv = output_dir_path / f"{file_stem}.partial.csv"
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        if mode == "localized":
            spec_groups: dict[tuple[str, str, str, str], list[dict]] = {}
            for row_dict in row_dicts:
                spec_groups.setdefault(spec_run_key(row_dict), []).append(row_dict)
            grouped_rows = list(spec_groups.values())
            print(f"Localized mode deduplicated {len(row_dicts)} benchmark rows into {len(grouped_rows)} unique specifications")
            future_to_index = {
                executor.submit(
                    evaluate_bug_check_localized_group,
                    detector,
                    group_rows,
                    idx % max_workers,
                    max_candidates_to_audit,
                ): idx
                for idx, group_rows in enumerate(grouped_rows)
            }
        else:
            future_to_index = {
                executor.submit(evaluate_bug_check_targeted, detector, row_dict, idx % max_workers): idx
                for idx, row_dict in enumerate(row_dicts)
            }

        for completed_idx, future in enumerate(as_completed(future_to_index), 1):
            row_idx = future_to_index[future]
            result = future.result()
            if mode == "localized":
                group_rows = grouped_rows[row_idx]
                result_rows = result["rows"]
                records.extend(result_rows)
                all_violations = sum(
                    1 for row in result["audited_candidates"] if normalize_bool(row.get("has_violation", False))
                )
                print(
                    f"[{completed_idx}/{len(grouped_rows)}] "
                    f"{result['seed patch']} :: {result['detected target']} "
                    f"-> benchmark_rows={result['bug_row_count']} "
                    f"(candidates={result['candidate_count']}, audited={len(result['audited_candidates'])}, violations={all_violations})"
                )
                expected_function_set = set(result["expected_buggy_functions"])
                for audited_row in result["audited_candidates"]:
                    audited_function = str(audited_row.get("audited_function", "")).strip() or "<none>"
                    audited_violation = "VIOLATION" if normalize_bool(audited_row.get("has_violation", False)) else "OK"
                    expected_marker = " [expected]" if audited_function in expected_function_set else ""
                    confidence = str(audited_row.get("confidence", "")).strip()
                    confidence_suffix = f" [{confidence}]" if confidence and audited_violation == "VIOLATION" else ""
                    print(f"    - {audited_function}{expected_marker} -> {audited_violation}{confidence_suffix}")
                    localized_audit_records.append(
                        {
                            "seed patch": result["seed patch"],
                            "detected target": result["detected target"],
                            "spec_target": result["spec_target"],
                            "spec_predicate": result["spec_predicate"],
                            "bug_row_count": result["bug_row_count"],
                            "expected_buggy_functions": ";".join(result["expected_buggy_functions"]),
                            "candidate_count": result["candidate_count"],
                            "audited_candidate_count": len(result["audited_candidates"]),
                            "audited_function": audited_row.get("audited_function", ""),
                            "is_expected_for_any_bug_row": audited_function in expected_function_set,
                            "has_violation": audited_row.get("has_violation", False),
                            "confidence": audited_row.get("confidence", ""),
                            "analysis": audited_row.get("analysis", ""),
                        }
                    )
            else:
                row_dict = row_dicts[row_idx]
                records.append(result)
                violation = "VIOLATION" if normalize_bool(result["has_violation"]) else "OK"
                print(
                    f"[{completed_idx}/{len(row_dicts)}] "
                    f"{row_dict['seed patch']} :: {row_dict['detected target']} :: {row_dict['buggy function']} -> {violation}"
                )

            if completed_idx % 10 == 0 or completed_idx == len(row_dicts):
                pd.DataFrame(records).sort_values("_row_index").drop(columns=["_row_index"]).to_csv(partial_csv, index=False)

    result_df = pd.DataFrame(records).sort_values("_row_index").drop(columns=["_row_index"])
    result_csv = output_dir_path / f"{file_stem}.csv"
    result_df.to_csv(result_csv, index=False)

    if mode == "localized":
        localized_audit_df = pd.DataFrame(localized_audit_records)
        all_audited_candidates_csv = output_dir_path / "reproduced_bug_detection_localized_all_audited_candidates.csv"
        localized_audit_df.to_csv(all_audited_candidates_csv, index=False)
        violation_reports_df = localized_audit_df[localized_audit_df["has_violation"].apply(normalize_bool)].copy()
        additional_violation_reports_df = violation_reports_df[
            ~violation_reports_df["is_expected_for_any_bug_row"].astype(bool)
        ].copy()
        violation_reports_csv = output_dir_path / "reproduced_bug_detection_localized_violation_reports.csv"
        additional_violation_reports_csv = (
            output_dir_path / "reproduced_bug_detection_localized_additional_violation_reports.csv"
        )
        violation_reports_df.to_csv(violation_reports_csv, index=False)
        additional_violation_reports_df.to_csv(additional_violation_reports_csv, index=False)

        benchmark_summary_rows = []
        for benchmark_key_value, group_df in result_df.groupby(["seed patch", "detected target", "buggy function"], sort=False):
            seed_patch, detected_target, buggy_function = benchmark_key_value
            benchmark_summary_rows.append(
                {
                    "seed patch": seed_patch,
                    "detected target": detected_target,
                    "buggy function": buggy_function,
                    "expected_function_found": bool(group_df["expected_function_found"].iloc[0]),
                    "expected_function_in_default_budget": bool(group_df["expected_function_in_default_budget"].iloc[0]),
                    "expected_function_forced_into_audit_set": bool(group_df["expected_function_forced_into_audit_set"].iloc[0]),
                    "expected_bug_detected": bool(
                        (
                            group_df["is_expected_buggy_function"].astype(bool)
                            & group_df["has_violation"].apply(normalize_bool)
                        ).any()
                    ),
                }
            )
        benchmark_view = pd.DataFrame(benchmark_summary_rows)
        per_seed_summary = (
            benchmark_view.groupby("seed patch")
            .agg(
                evaluated_bug_rows=("buggy function", "size"),
                localized_bug_rows=("expected_function_found", lambda values: int(sum(bool(v) for v in values))),
                detected_bug_rows=("expected_bug_detected", lambda values: int(sum(bool(v) for v in values))),
            )
            .reset_index()
        )
        summary = build_localized_bug_detection_summary(
            result_df.to_dict("records"),
            generation_seed_count=int(pd.read_csv(GENERATION_SEEDS).shape[0]) if GENERATION_SEEDS.exists() else None,
        )
        summary["benchmark_bug_rows"] = int(len(checks_df))
        summary["audited_specification_rows"] = int(len(grouped_rows))
        summary["audited_candidate_rows"] = int(len(localized_audit_df))
        summary["violation_report_rows"] = int(len(violation_reports_df))
        summary["additional_violation_report_rows"] = int(len(additional_violation_reports_df))
        summary["reference_files"] = {
            "benchmark_dataset": display_path(DATASET),
        }
        summary_csv = output_dir_path / "reproduced_bug_detection_localized_summary.csv"
        summary_path = output_dir_path / "reproduced_bug_detection_localized_summary.json"
    else:
        per_seed_summary = (
            result_df.groupby("seed patch")
            .agg(
                evaluated_bug_rows=("buggy function", "size"),
                detected_bug_rows=("has_violation", lambda values: int(sum(normalize_bool(v) for v in values))),
            )
            .reset_index()
        )
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
                "benchmark_dataset": display_path(DATASET),
                "reference_results": display_path(REFERENCE_RESULTS),
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
        summary_csv = output_dir_path / "reproduced_bug_detection_summary.csv"
        summary_path = output_dir_path / "reproduced_bug_detection_summary.json"

    per_seed_summary.to_csv(summary_csv, index=False)
    write_summary(summary_path, summary)

    return {
        "mode": mode,
        "result_csv": str(result_csv),
        "summary_csv": str(summary_csv),
        "summary_json": str(summary_path),
        **(
            {
                "all_audited_candidates_csv": str(all_audited_candidates_csv),
                "violation_reports_csv": str(violation_reports_csv),
                "additional_violation_reports_csv": str(additional_violation_reports_csv),
            }
            if mode == "localized"
            else {}
        ),
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
    parser.add_argument(
        "--mode",
        choices=["localized", "targeted", "probe"],
        default="localized",
        help="Bug benchmark mode: localized runs localization plus auditing, probe only measures localization coverage, targeted directly audits expected buggy functions",
    )
    parser.add_argument(
        "--max-candidates-to-audit",
        type=int,
        default=50,
        help="Maximum number of localized candidates to send to the LLM per specification",
    )
    args = parser.parse_args()

    result = run_reproduced_bug_detection(
        kernel_path=args.kernel_path,
        output_dir=args.output_dir,
        model=args.model,
        max_workers=args.max_workers,
        mode=args.mode,
        max_candidates_to_audit=args.max_candidates_to_audit,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
