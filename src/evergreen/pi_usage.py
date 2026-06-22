"""Helpers for reading Pi session usage metadata."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _jsonl_files(session_dir: Path | str) -> list[Path]:
    return sorted(Path(session_dir).glob("*.jsonl"))


def session_cost(session_dir: Path | str) -> tuple[float | None, int | None]:
    """Sum usage cost + tokens across a Pi session's JSONL transcript.

    Pi records usage per assistant message under `message.usage` or another nested
    `usage` object. Returns `(None, None)` when no transcript exists.
    """
    files = _jsonl_files(session_dir)
    if not files:
        return None, None

    cost = 0.0
    tokens = 0

    def walk(x: Any):
        nonlocal cost, tokens
        if isinstance(x, dict):
            u = x.get("usage")
            if isinstance(u, dict) and isinstance(u.get("cost"), dict):
                cost += u["cost"].get("total", 0) or 0
                tokens += u.get("totalTokens", 0) or 0
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)

    for f in files:
        for line in f.read_text(errors="replace").splitlines():
            try:
                walk(json.loads(line))
            except (json.JSONDecodeError, ValueError):
                continue

    return round(cost, 6), tokens


def session_model_effort(session_dir: Path | str) -> tuple[str | None, str | None]:
    """Read the primary model + thinking level from a Pi session transcript."""
    files = _jsonl_files(session_dir)
    if not files:
        return None, None

    model = effort = None
    for f in files:
        for line in f.read_text(errors="replace").splitlines():
            try:
                o = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            if model is None and o.get("type") == "model_change":
                model = o.get("modelId")
            if effort is None and o.get("type") == "thinking_level_change":
                effort = o.get("thinkingLevel")
            if model and effort:
                return model, effort
    return model, effort


def session_metrics(session_dir: Path | str) -> tuple[float | None, int | None, str | None, str | None]:
    """Return `(cost, tokens, model, effort)` for a Pi session directory."""
    cost, tokens = session_cost(session_dir)
    model, effort = session_model_effort(session_dir)
    return cost, tokens, model, effort
