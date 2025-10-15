from __future__ import annotations

import json
import logging
from typing import Any

from json_repair import repair_json


def extract_json_block(text: str) -> str:
    """Return the JSON object embedded in a model response.

    Handles common patterns such as fenced code blocks while preserving the
    original payload when no JSON object can be isolated.
    """
    candidate = text.strip()
    if candidate.startswith("```"):
        lines = candidate.splitlines()
        if len(lines) >= 2 and lines[0].startswith("```"):
            closing_index = len(lines) - 1 if lines[-1].startswith("```") else len(lines)
            candidate = "\n".join(lines[1:closing_index])
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start != -1 and end != -1 and end >= start:
        return candidate[start : end + 1]
    return candidate


def load_json_with_repair(
    raw: str,
    *,
    logger: logging.Logger,
    repair_log_level: int = logging.WARNING,
) -> Any:
    """Best-effort JSON loader that optionally repairs malformed payloads."""
    cleaned = extract_json_block(raw)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as exc:
        logger.log(
            repair_log_level,
            "Primary JSON parse failed, attempting repair: %s",
            exc,
        )
        try:
            repaired = repair_json(cleaned)
            return json.loads(repaired)
        except Exception as repair_exc:
            logger.error("JSON repair failed: %s", repair_exc)
            raise exc from repair_exc
