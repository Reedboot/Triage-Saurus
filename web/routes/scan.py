from __future__ import annotations

from .registry import build_blueprint_creator

ENDPOINTS = {
    "api_scan_log",
    "api_scans",
    "api_detect_modules",
    "api_register_module_scan",
    "scan",
}
create_blueprint = build_blueprint_creator("scan_routes", __name__, ENDPOINTS)
