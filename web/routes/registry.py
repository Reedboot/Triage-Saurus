from __future__ import annotations

from dataclasses import dataclass
from types import ModuleType
from typing import Any

from flask import Blueprint


@dataclass(frozen=True)
class RouteDef:
    rule: str
    endpoint: str
    options: dict[str, Any]


class RouteCollector:
    def __init__(self) -> None:
        self._routes: list[RouteDef] = []

    def route(self, rule: str, **options: Any):
        def decorator(func):
            self._routes.append(RouteDef(rule=rule, endpoint=func.__name__, options=dict(options)))
            return func

        return decorator

    @property
    def routes(self) -> tuple[RouteDef, ...]:
        return tuple(self._routes)


def create_domain_blueprint(
    blueprint_name: str,
    import_name: str,
    handlers: ModuleType,
    collector: RouteCollector,
    endpoint_names: set[str],
) -> Blueprint:
    blueprint = Blueprint(blueprint_name, import_name)
    for route in collector.routes:
        if route.endpoint not in endpoint_names:
            continue
        view_func = getattr(handlers, route.endpoint)
        add_kwargs = dict(route.options)
        blueprint.add_url_rule(route.rule, endpoint=route.endpoint, view_func=view_func, **add_kwargs)
    return blueprint


def build_blueprint_creator(
    blueprint_name: str,
    import_name: str,
    endpoint_names: set[str],
):
    def create_blueprint(handlers: ModuleType, collector: RouteCollector):
        return create_domain_blueprint(blueprint_name, import_name, handlers, collector, endpoint_names)

    return create_blueprint
