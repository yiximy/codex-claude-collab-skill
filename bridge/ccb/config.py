"""Runtime config: env vars + ~/.ai-bridge/config.json + defaults.

Precedence: env var > config.json > built-in default.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from .paths import CONFIG_FILE

# CC Switch / Codex Desktop rewrite this file on every provider switch;
# the bridge follows its top-level `model` so model switching stays in CC Switch.
CODEX_CONFIG_TOML = Path.home() / ".codex" / "config.toml"

_DEFAULTS: dict[str, Any] = {
    "codex_model": "gpt-5.5",
    "codex_model_follow_codex_config": True,
    "codex_reasoning_effort": "high",
    "codex_reasoning_summary": "auto",
    "codex_fast_mode": True,
    "default_timeout_sec": 1800,
    "enable_multi_account": False,
    "summary_tail_enabled": True,
    "cwd_remaps": [],
    "default_quota_ttl_hours": 5,
    "default_ban_ttl_hours": 24,
    "show_codex_trace": False,
    "auto_trust_cwd": True,
}

_ENV_MAP = {
    "codex_model": "CCB_CODEX_MODEL",
    "codex_model_follow_codex_config": "CCB_FOLLOW_CODEX_CONFIG",
    "codex_reasoning_effort": "CCB_REASONING_EFFORT",
    "codex_reasoning_summary": "CCB_REASONING_SUMMARY",
    "codex_fast_mode": "CCB_FAST_MODE",
    "default_timeout_sec": "CCB_DEFAULT_TIMEOUT",
    "enable_multi_account": "CCB_ENABLE_ROTATION",
    "summary_tail_enabled": "CCB_SUMMARY_TAIL",
    "default_quota_ttl_hours": "CCB_QUOTA_TTL_HOURS",
    "default_ban_ttl_hours": "CCB_BAN_TTL_HOURS",
    "show_codex_trace": "CCB_SHOW_TRACE",
    "auto_trust_cwd": "CCB_AUTO_TRUST_CWD",
}


def _coerce(default: Any, raw: str) -> Any:
    if isinstance(default, bool):
        return raw.strip().lower() in ("1", "true", "yes", "on")
    if isinstance(default, int):
        try:
            return int(raw)
        except ValueError:
            return default
    if isinstance(default, list):
        return [s.strip() for s in raw.split(",") if s.strip()]
    return raw


def _load_file_cfg() -> dict[str, Any]:
    if not CONFIG_FILE.exists():
        return {}
    try:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


_FILE_CFG = _load_file_cfg()


def get(key: str) -> Any:
    """Resolve a config value: env > config.json > default."""
    default = _DEFAULTS[key]
    env_name = _ENV_MAP.get(key)
    if env_name:
        raw = os.environ.get(env_name)
        if raw is not None:
            return _coerce(default, raw)
    if key in _FILE_CFG:
        return _FILE_CFG[key]
    return default


def cwd_remaps() -> list[tuple[str, str]]:
    """List of (src_prefix, dst_prefix) for codex CWD ASCII-junction remapping.

    Codex CLI passes cwd in HTTP headers; non-ASCII characters trigger upstream
    retry-5-times-then-give-up. Users with non-ASCII paths can map them to a
    pre-created ASCII junction (e.g. mklink /J C:\\plywork D:\\my-work).

    Env: CCB_CWD_REMAPS="C:\\src1=C:\\dst1,C:\\src2=C:\\dst2"
    """
    env = os.environ.get("CCB_CWD_REMAPS", "")
    pairs: list[tuple[str, str]] = []
    if env:
        for token in env.split(","):
            if "=" in token:
                s, d = token.split("=", 1)
                s, d = s.strip(), d.strip()
                if s and d:
                    pairs.append((s, d))
    file_pairs = _FILE_CFG.get("cwd_remaps") or []
    for entry in file_pairs:
        if isinstance(entry, list) and len(entry) == 2:
            pairs.append((entry[0], entry[1]))
        elif isinstance(entry, dict) and "src" in entry and "dst" in entry:
            pairs.append((entry["src"], entry["dst"]))
    return pairs

def _read_codex_toml_model() -> str | None:
    """Return the top-level `model` from ~/.codex/config.toml, or None.

    CC Switch / Codex Desktop rewrite this file on every provider switch, so
    reading it here makes the bridge follow the user's CC Switch selection
    instead of a hard-coded value in ~/.ai-bridge/config.json.
    """
    try:
        if not CODEX_CONFIG_TOML.exists():
            return None
        text = CODEX_CONFIG_TOML.read_text(encoding="utf-8")
    except Exception:
        return None
    try:
        import tomllib  # Python 3.11+

        data = tomllib.loads(text)
        model = data.get("model")
        if isinstance(model, str) and model.strip():
            return model.strip()
    except Exception:
        pass
    # Fallback for older runtimes: top-level `model = "..."` line only.
    m = re.search(r"(?m)^model\s*=\s*\"([^\"]+)\"\s*$", text)
    if m:
        return m.group(1).strip()
    return None


def resolve_codex_model() -> str:
    """Pick the model used when spawning Codex.

    Default (codex_model_follow_codex_config=true): follow the top-level
    `model` in ~/.codex/config.toml, which CC Switch / Codex Desktop write on
    provider switch - switch the model in CC Switch and it applies here with
    no manual config edit. Falls back to the legacy chain
    (env CCB_CODEX_MODEL > ~/.ai-bridge/config.json > built-in default) when
    the TOML has no model or following is disabled (CCB_FOLLOW_CODEX_CONFIG=0).
    """
    if get("codex_model_follow_codex_config"):
        from_toml = _read_codex_toml_model()
        if from_toml:
            return from_toml
    return str(get("codex_model"))
