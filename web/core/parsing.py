from __future__ import annotations

import json
from typing import Any


def parse_json_value(value: object, *, default: Any):
    if value is None:
        return default
    if isinstance(default, dict):
        if isinstance(value, dict):
            return value
    elif isinstance(default, list):
        if isinstance(value, list):
            return value
    else:
        if isinstance(value, type(default)):
            return value

    text = str(value).strip()
    if not text:
        return default
    try:
        parsed = json.loads(text)
    except Exception:
        return default
    return parsed


def parse_json_list(value: object) -> list:
    parsed = parse_json_value(value, default=[])
    if isinstance(parsed, list):
        return parsed
    if isinstance(parsed, dict):
        return [parsed]
    return []


def parse_json_dict(value: object) -> dict:
    parsed = parse_json_value(value, default={})
    return parsed if isinstance(parsed, dict) else {}


def parse_json_string_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    parsed = parse_json_list(value)
    return [str(item).strip() for item in parsed if str(item).strip()]
