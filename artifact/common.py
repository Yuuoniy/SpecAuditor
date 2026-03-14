#!/usr/bin/env python3
"""Shared helpers for reviewer-facing artifact workflows."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from glob import glob
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

def latest_matching_file(pattern: str) -> Path:
    matches = [Path(path) for path in glob(pattern)]
    if not matches:
        raise FileNotFoundError(f"No files matched: {pattern}")
    matches.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    return matches[0]


def run_command(args, env=None):
    result = subprocess.run(args, cwd=ROOT, env=env, text=True)
    if result.returncode != 0:
        raise subprocess.CalledProcessError(result.returncode, args)


def ensure_env(model: str):
    if not os.getenv("OPENAI_API_KEY"):
        raise ValueError("OPENAI_API_KEY is required for live LLM stages")
    if model == "claude-sonnet-4-20250514" and not os.getenv("OPENAI_BASE_URL"):
        raise ValueError("OPENAI_BASE_URL is required when using claude-sonnet-4-20250514")


def ensure_git_safe_directory(path: str | os.PathLike[str]) -> None:
    resolved = str(Path(path).resolve())
    subprocess.run(
        ["git", "config", "--global", "--add", "safe.directory", resolved],
        cwd=ROOT,
        text=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )


def filter_stage3_rows_for_targets(rows, target_allowlist):
    filtered_rows = []
    allowed = set(target_allowlist)

    for row in rows:
        similar_targets = json.loads(row.get("similar_target_list", "[]") or "[]")
        descriptions = json.loads(row.get("target_descriptions", "{}") or "{}")
        scores = json.loads(row.get("similarity_scores", "{}") or "{}")

        kept_targets = [target for target in similar_targets if target in allowed]
        kept_descriptions = {target: descriptions[target] for target in kept_targets if target in descriptions}
        kept_scores = {target: scores[target] for target in kept_targets if target in scores}

        updated = dict(row)
        updated["similar_target_count"] = len(kept_targets)
        updated["similar_target_list"] = json.dumps(kept_targets, ensure_ascii=False)
        updated["target_descriptions"] = json.dumps(kept_descriptions, ensure_ascii=False)
        updated["similarity_scores"] = json.dumps(kept_scores, ensure_ascii=False)
        filtered_rows.append(updated)

    return filtered_rows


def build_stage4_summary(stage4_df: pd.DataFrame) -> dict:
    if stage4_df.empty:
        return {
            "stage4_rows": 0,
            "stage4_unique_targets": 0,
            "stage4_unique_specs": 0,
            "per_seed_stage4_rows": {},
        }

    normalized = stage4_df.fillna("").copy()
    for col in ["hexsha", "similar_target", "spec_target"]:
        normalized[col] = normalized[col].astype(str).str.strip()

    per_seed = (
        normalized.groupby("hexsha")
        .agg(
            rows=("similar_target", "size"),
            unique_targets=("similar_target", "nunique"),
            unique_specs=("spec_target", "nunique"),
        )
        .reset_index()
    )

    return {
        "stage4_rows": int(len(normalized)),
        "stage4_unique_targets": int(normalized["similar_target"].nunique()),
        "stage4_unique_specs": int(normalized["spec_target"].nunique()),
        "per_seed_stage4_rows": {
            row["hexsha"]: {
                "rows": int(row["rows"]),
                "unique_targets": int(row["unique_targets"]),
                "unique_specs": int(row["unique_specs"]),
            }
            for row in per_seed.to_dict("records")
        },
    }


def write_summary(path: Path, payload: dict):
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
