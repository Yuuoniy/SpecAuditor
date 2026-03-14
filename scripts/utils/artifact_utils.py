#!/usr/bin/env python3
"""
Shared helpers for repository-relative artifact packaging logic.
"""

import os
import sys
from pathlib import Path
from typing import Iterable, List, Optional


def get_repo_root(start: Optional[os.PathLike] = None) -> Path:
    current = Path(start or __file__).resolve()
    if current.is_file():
        current = current.parent

    for candidate in [current, *current.parents]:
        if (candidate / "scripts").is_dir() and (candidate / "get_docs").is_dir():
            return candidate

    raise FileNotFoundError(f"Could not find repository root from {start or __file__}")


def get_scripts_dir(start: Optional[os.PathLike] = None) -> Path:
    return get_repo_root(start) / "scripts"


def get_utils_dir(start: Optional[os.PathLike] = None) -> Path:
    return get_scripts_dir(start) / "utils"


def get_prompts_dir(start: Optional[os.PathLike] = None) -> Path:
    return get_repo_root(start) / "prompts"


def get_chroma_dir(start: Optional[os.PathLike] = None) -> Path:
    return get_repo_root(start) / "get_docs" / "kernel_docs_chroma"


def get_tree_sitter_dir(start: Optional[os.PathLike] = None) -> Path:
    return get_scripts_dir(start) / "tree-sitter-c"


def get_build_library_path(start: Optional[os.PathLike] = None) -> Path:
    return get_scripts_dir(start) / "build" / "my-languages.so"


def configure_script_imports(script_file: os.PathLike) -> None:
    for path in (
        get_scripts_dir(script_file),
        get_utils_dir(script_file),
        get_prompts_dir(script_file),
    ):
        path_str = str(path)
        if path_str not in sys.path:
            sys.path.insert(0, path_str)


def parse_allowlist(values: Optional[str]) -> List[str]:
    if not values:
        return []

    seen = set()
    parsed = []
    for raw_item in values.split(","):
        item = raw_item.strip()
        if item and item not in seen:
            seen.add(item)
            parsed.append(item)
    return parsed


def load_allowlist(
    values: Optional[str] = None,
    file_path: Optional[os.PathLike] = None,
) -> List[str]:
    seen = set()
    allowlist: List[str] = []

    for item in parse_allowlist(values):
        if item not in seen:
            seen.add(item)
            allowlist.append(item)

    if file_path:
        for raw_line in Path(file_path).read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or line in seen:
                continue
            seen.add(line)
            allowlist.append(line)

    return allowlist


def filter_preserve_order(items: Iterable[str], allowlist: Optional[Iterable[str]]) -> List[str]:
    if not allowlist:
        return list(items)

    allowed = set(allowlist)
    return [item for item in items if item in allowed]


def resolve_env_value(value, required: bool = False):
    if value is None:
        if required:
            raise KeyError("Missing required environment-backed value")
        return None

    if not isinstance(value, str):
        return value

    if value.startswith("${") and value.endswith("}"):
        env_name = value[2:-1].strip()
        env_value = os.getenv(env_name)
        if env_value is None and required:
            raise KeyError(env_name)
        return env_value

    return value


def resolve_model_config(model_config: dict) -> dict:
    resolved = dict(model_config)

    env_backed_fields = {
        "api_key": resolved.pop("api_key_env", None),
        "base_url": resolved.pop("base_url_env", None),
    }

    for field, env_name in env_backed_fields.items():
        if env_name:
            env_value = os.getenv(env_name)
            if env_value:
                resolved[field] = env_value
            elif field == "api_key" and not resolved.get(field):
                raise KeyError(env_name)

    for field, value in list(resolved.items()):
        required = field == "api_key"
        try:
            resolved[field] = resolve_env_value(value, required=required)
        except KeyError:
            raise

    return resolved
