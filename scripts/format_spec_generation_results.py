#!/usr/bin/env python3
"""
Flatten raw stage4 specification-generation results into the row-oriented format
consumed by bug detection.
"""

import argparse
import json
from pathlib import Path

import pandas as pd


FORMATTED_COLUMNS = [
    "hexsha",
    "target",
    "predicate",
    "generalized_target",
    "generalized_predicate",
    "similar_target",
    "spec_target",
    "spec_predicate",
    "reason",
    "evidence",
    "similarity_score",
]


def _normalize_generated_specs(raw_specs):
    if isinstance(raw_specs, str):
        if not raw_specs.strip():
            return {}
        return json.loads(raw_specs)
    return raw_specs or {}


def flatten_spec_generation_rows(records):
    rows = []

    for item in records:
        generated_specs = _normalize_generated_specs(item.get("generated_specifications", {}))

        for similar_target, spec_info in generated_specs.items():
            specification_obj = spec_info.get("specification", {})
            if isinstance(specification_obj, str):
                spec_target = similar_target
                spec_predicate = specification_obj
            else:
                spec_target = specification_obj.get("target", "")
                spec_predicate = specification_obj.get("specification", "") or specification_obj.get("predicate", "")

            rows.append(
                {
                    "hexsha": item.get("hexsha", ""),
                    "target": item.get("target", ""),
                    "predicate": item.get("predicate", ""),
                    "generalized_target": item.get("generalized_target", ""),
                    "generalized_predicate": item.get("generalized_predicate", ""),
                    "similar_target": similar_target,
                    "spec_target": spec_target,
                    "spec_predicate": spec_predicate,
                    "reason": spec_info.get("reason", ""),
                    "evidence": json.dumps(spec_info.get("evidence", []), ensure_ascii=False),
                    "similarity_score": spec_info.get("similarity_score", 0.0),
                }
            )

    return rows


def build_formatted_dataframe(records):
    return pd.DataFrame(flatten_spec_generation_rows(records), columns=FORMATTED_COLUMNS)


def _default_output_path(input_path: Path) -> Path:
    return input_path.with_name(f"{input_path.stem}_formatted.csv")


def main():
    parser = argparse.ArgumentParser(description="Format raw stage4 results into a flat CSV")
    parser.add_argument("input_path", help="Raw stage4 CSV or JSON file")
    parser.add_argument("--output", default=None, help="Formatted output CSV path")
    args = parser.parse_args()

    input_path = Path(args.input_path)
    output_path = Path(args.output) if args.output else _default_output_path(input_path)

    if input_path.suffix.lower() == ".json":
        records = json.loads(input_path.read_text(encoding="utf-8"))
    else:
        records = pd.read_csv(input_path).to_dict("records")

    build_formatted_dataframe(records).to_csv(output_path, index=False)
    print(f"Formatted CSV written to: {output_path}")


if __name__ == "__main__":
    main()
