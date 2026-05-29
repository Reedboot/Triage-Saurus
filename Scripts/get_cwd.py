#!/usr/bin/env python3
"""
Resolve and print the current working directory.

Used by agents to orient themselves at the start of a session.
"""

import sys
from pathlib import Path


def get_cwd() -> Path:
    """Return the resolved repo root (directory containing this script's parent)."""
    return Path(__file__).resolve().parent


def main() -> None:
    print(get_cwd())


if __name__ == "__main__":
    main()
