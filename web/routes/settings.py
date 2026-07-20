from __future__ import annotations

from .registry import build_blueprint_creator

ENDPOINTS = {
    "settings_page",
    "api_settings_get",
    "api_settings_post",
    "api_settings_cloud_cache_clear",
}
create_blueprint = build_blueprint_creator("settings_routes", __name__, ENDPOINTS)
