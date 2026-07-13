"""Shared configuration and settings helpers for the web UI."""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "Scripts"
PIPELINE = SCRIPTS / "Utils" / "run_pipeline.py"
ENRICH_FINDINGS = SCRIPTS / "Enrich" / "enrich_findings.py"
RUN_SKEPTICS = SCRIPTS / "Utils" / "run_skeptics.py"
GENERATE_PROJECT_OVERVIEW = SCRIPTS / "Enrich" / "generate_project_overview.py"
EXPERIMENTS_DIR = REPO_ROOT / "Output" / "Learning" / "experiments"
INTAKE_REPOS = REPO_ROOT / "Intake" / "ReposToScan.txt"
DB_PATH = REPO_ROOT / "Output" / "Data" / "cozo.db"
APP_SETTINGS_PATH = REPO_ROOT / "Settings" / "app_settings.json"

_APP_SETTINGS_DEFAULT: dict = {
    "ai_models": {
        "global_default": "gpt-5.4-mini",
        "architecture": "",
        "overview": "",
        "rules": "",
        "themes": "",
        "image_summaries": "",
    },
    "ai_behaviour": {
        "review_mode": "minimal",
        "enable_themes": False,
        "enable_image_summaries": False,
        "stream_timeout_seconds": 600,
        "job_timeout_seconds": 3600,
    },
    "performance": {
        "module_scan_concurrency": 1,
        "pipeline_parallel_mode": True,
        "single_writer_queue_enabled": True,
    },
}

_app_settings_cache: dict | None = None


def _load_app_settings(force: bool = False) -> dict:
    """Load Settings/app_settings.json, merging over defaults. Result is cached."""
    global _app_settings_cache
    if _app_settings_cache is not None and not force:
        return _app_settings_cache
    result = copy.deepcopy(_APP_SETTINGS_DEFAULT)
    if APP_SETTINGS_PATH.exists():
        try:
            data = json.loads(APP_SETTINGS_PATH.read_text(encoding="utf-8"))
            for section, values in data.items():
                if section.startswith("_"):
                    continue
                if isinstance(values, dict) and section in result:
                    result[section].update(values)
        except Exception:
            pass
    _app_settings_cache = result
    return result


def _save_app_settings(data: dict) -> None:
    """Persist settings to Settings/app_settings.json and invalidate cache."""
    global _app_settings_cache
    APP_SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    existing: dict = {}
    if APP_SETTINGS_PATH.exists():
        try:
            existing = json.loads(APP_SETTINGS_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    notes = existing.get("_notes", "Triage-Saurus runtime settings. Edit via ⚙ Settings in the web UI.")
    out = {"_notes": notes}
    out.update(data)
    APP_SETTINGS_PATH.write_text(json.dumps(out, indent=2), encoding="utf-8")
    _app_settings_cache = None


def _settings_get(section: str, key: str):
    """Return a setting value from the loaded settings dict."""
    return _load_app_settings().get(section, {}).get(key)


def _feature_flag_enabled(name: str, default: bool = True) -> bool:
    """Check env var first, then settings file, then coded default."""
    raw = os.environ.get(name)
    if raw is not None:
        return raw.strip().lower() in {"1", "true", "yes", "on", "enabled"}
    env_to_setting: dict[str, tuple[str, str]] = {
        "TRIAGE_SINGLE_WRITER_QUEUE_ENABLED": ("performance", "single_writer_queue_enabled"),
        "TRIAGE_PIPELINE_PARALLEL_MODE": ("performance", "pipeline_parallel_mode"),
        "TRIAGE_AI_ENABLE_THEMES": ("ai_behaviour", "enable_themes"),
        "TRIAGE_AI_ENABLE_IMAGE_SUMMARIES": ("ai_behaviour", "enable_image_summaries"),
    }
    if name in env_to_setting:
        section, key = env_to_setting[name]
        val = _settings_get(section, key)
        if val is not None:
            return bool(val)
    return default


def _feature_flag_int(name: str, default: int, minimum: int = 1) -> int:
    """Check env var first, then settings file, then coded default."""
    raw = os.environ.get(name)
    if raw is not None:
        try:
            return max(minimum, int(raw))
        except (TypeError, ValueError):
            return default
    env_to_setting: dict[str, tuple[str, str]] = {
        "TRIAGE_MODULE_SCAN_CONCURRENCY": ("performance", "module_scan_concurrency"),
        "AI_STREAM_TIMEOUT": ("ai_behaviour", "stream_timeout_seconds"),
        "AI_JOB_TIMEOUT": ("ai_behaviour", "job_timeout_seconds"),
    }
    if name in env_to_setting:
        section, key = env_to_setting[name]
        val = _settings_get(section, key)
        if val is not None:
            try:
                return max(minimum, int(val))
            except (TypeError, ValueError):
                pass
    return default


def _scan_feature_flags() -> dict[str, object]:
    return {
        "module_scan_concurrency": _feature_flag_int("TRIAGE_MODULE_SCAN_CONCURRENCY", default=1, minimum=1),
        "single_writer_queue_enabled": _feature_flag_enabled("TRIAGE_SINGLE_WRITER_QUEUE_ENABLED", default=True),
        "pipeline_parallel_mode_enabled": _feature_flag_enabled("TRIAGE_PIPELINE_PARALLEL_MODE", default=True),
    }


def _env_model(name: str, fallback: str = "gpt-5.4-mini") -> str:
    """Resolve a model: env var → settings file → global default → coded fallback."""
    value = (os.environ.get(name) or "").strip()
    if value:
        return value
    env_to_setting: dict[str, tuple[str, str]] = {
        "COPILOT_MODEL": ("ai_models", "global_default"),
        "COPILOT_MODEL_ARCHITECTURE": ("ai_models", "architecture"),
        "COPILOT_MODEL_OVERVIEW": ("ai_models", "overview"),
        "COPILOT_MODEL_RULES": ("ai_models", "rules"),
        "COPILOT_MODEL_THEMES": ("ai_models", "themes"),
        "COPILOT_MODEL_IMAGE_SUMMARIES": ("ai_models", "image_summaries"),
    }
    if name in env_to_setting:
        section, key = env_to_setting[name]
        slot_val = (_settings_get(section, key) or "").strip()
        if slot_val:
            return slot_val
    global_env = (os.environ.get("COPILOT_MODEL") or "").strip()
    if global_env:
        return global_env
    global_setting = (_settings_get("ai_models", "global_default") or "").strip()
    if global_setting:
        return global_setting
    return fallback


def _load_search_paths() -> list[Path]:
    config_file = REPO_ROOT / "Settings" / "paths.json"
    if config_file.exists():
        try:
            config = json.loads(config_file.read_text())
            paths = config.get("repo_search_paths", [])
            return [Path(p).expanduser() for p in paths]
        except Exception:
            pass
    return [
        REPO_ROOT.parent,
        Path.home() / "repos",
        Path.home() / "code",
        Path.home() / "projects",
        Path.home(),
    ]


_SEARCH_ROOTS = _load_search_paths()
