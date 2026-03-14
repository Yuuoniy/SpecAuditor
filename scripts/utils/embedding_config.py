#!/usr/bin/env python3
"""
Embedding configuration loader with YAML defaults and optional local env file support.
"""

import os
from pathlib import Path
from typing import Dict, Any, Optional

import yaml

try:
    from .artifact_utils import get_repo_root
except ImportError:
    from artifact_utils import get_repo_root


def _default_config_path() -> Path:
    return Path(__file__).with_name("embedding_config.yaml")


def _default_env_path(start: Optional[os.PathLike] = None) -> Path:
    return get_repo_root(start or __file__) / "artifact" / "config" / "embedding.env"


def _load_yaml(config_path: Path) -> Dict[str, Any]:
    with open(config_path, "r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def _strip_env_value(raw_value: str) -> str:
    value = raw_value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def load_local_embedding_env(
    start: Optional[os.PathLike] = None,
    override: bool = False,
) -> Optional[Path]:
    env_path = _default_env_path(start)
    if not env_path.is_file():
        return None

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        if override or key not in os.environ:
            os.environ[key] = _strip_env_value(value)
    return env_path


def resolve_embedding_config(
    start: Optional[os.PathLike] = None,
    config_path: Optional[os.PathLike] = None,
) -> Dict[str, str]:
    load_local_embedding_env(start=start)

    config = _load_yaml(Path(config_path) if config_path else _default_config_path())
    embedding_cfg = config.get("embedding", {})

    api_key_env = embedding_cfg.get("api_key_env", "EMBEDDING_API_KEY")
    legacy_api_key_env = embedding_cfg.get("legacy_api_key_env", "SILICONFLOW_API_KEY")
    base_url_env = embedding_cfg.get("base_url_env", "EMBEDDING_BASE_URL")
    model_env = embedding_cfg.get("model_env", "EMBEDDING_MODEL")

    api_key = os.getenv(api_key_env) or os.getenv(legacy_api_key_env)
    if not api_key:
        raise KeyError(api_key_env)

    return {
        "api_key": api_key,
        "base_url": os.getenv(base_url_env, embedding_cfg.get("default_base_url", "")),
        "model": os.getenv(model_env, embedding_cfg.get("default_model", "")),
        "api_key_env": api_key_env,
        "legacy_api_key_env": legacy_api_key_env,
        "base_url_env": base_url_env,
        "model_env": model_env,
    }
