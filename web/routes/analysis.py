from __future__ import annotations

from .registry import build_blueprint_creator

ENDPOINTS = {
    "api_experiment_repo",
    "api_analysis_start",
    "api_analysis_resume",
    "api_analysis_status",
    "api_analysis_copilot_stream",
    "api_analysis_generate_rules",
    "api_analysis_stop",
}
create_blueprint = build_blueprint_creator("analysis_routes", __name__, ENDPOINTS)
