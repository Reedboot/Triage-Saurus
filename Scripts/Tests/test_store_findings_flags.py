#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "Scripts" / "Persist"))

import store_findings  # noqa: E402


def test_single_writer_queue_flag_defaults_to_enabled(monkeypatch):
    monkeypatch.delenv("TRIAGE_SINGLE_WRITER_QUEUE_ENABLED", raising=False)
    assert store_findings._single_writer_queue_enabled() is True


def test_single_writer_queue_flag_respects_false_values(monkeypatch):
    monkeypatch.setenv("TRIAGE_SINGLE_WRITER_QUEUE_ENABLED", "0")
    assert store_findings._single_writer_queue_enabled() is False


def test_single_writer_queue_flag_respects_true_values(monkeypatch):
    monkeypatch.setenv("TRIAGE_SINGLE_WRITER_QUEUE_ENABLED", "true")
    assert store_findings._single_writer_queue_enabled() is True
