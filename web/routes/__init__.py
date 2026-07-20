from __future__ import annotations

from types import ModuleType

from flask import Flask

from . import analysis, cloud, export, pages, scan, settings, view
from .registry import RouteCollector

_DOMAIN_MODULES = (analysis, view, cloud, scan, settings, export, pages)


def register_route_blueprints(app: Flask, handlers: ModuleType, collector: RouteCollector) -> None:
    assigned: set[str] = set()
    for domain_module in _DOMAIN_MODULES:
        blueprint = domain_module.create_blueprint(handlers, collector)
        app.register_blueprint(blueprint)
        assigned.update(domain_module.ENDPOINTS)

    collected = {route.endpoint for route in collector.routes}
    missing = sorted(collected - assigned)
    if missing:
        missing_text = ", ".join(missing)
        raise RuntimeError(f"Unassigned routes detected; add endpoint mappings: {missing_text}")


__all__ = ["RouteCollector", "register_route_blueprints"]
