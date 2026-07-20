from __future__ import annotations

from flask import request


def _analysis_mode_from_request() -> str:
    return (request.args.get("mode") or "").strip().lower()


def _force_rerun_requested() -> bool:
    raw = (request.args.get("force") or "").strip().lower()
    return raw in {"1", "true", "yes", "on", "enabled"}

