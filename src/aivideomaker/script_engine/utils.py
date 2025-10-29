from __future__ import annotations

import json
import logging
from typing import Any

from json_repair import repair_json


def extract_json_block(text: str) -> str:
    """Return the JSON object embedded in a model response.

    Handles fenced code blocks and discards chatter that may precede or follow
    the JSON payload. Falls back to the original text when no balanced object
    can be isolated.
    """

    def _strip_code_fence(block: str) -> str:
        stripped = block.strip()
        if not stripped.startswith("```"):
            return stripped
        lines = stripped.splitlines()
        if len(lines) < 2:
            return stripped
        closing_index = next(
            (idx for idx, line in enumerate(lines[1:], start=1) if line.startswith("```")),
            len(lines),
        )
        inner = "\n".join(lines[1:closing_index]).strip()
        return inner or stripped

    def _extract_object(source: str, start_idx: int) -> str | None:
        depth = 0
        in_string = False
        escaped = False
        start = None
        for idx in range(start_idx, len(source)):
            char = source[idx]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '{':
                depth += 1
                if depth == 1:
                    start = idx
            elif char == '}':
                depth -= 1
                if depth == 0 and start is not None:
                    return source[start : idx + 1]
            elif char == '"':
                in_string = True
        return None

    candidate = _strip_code_fence(text)
    if not candidate:
        return candidate

    trimmed = candidate.strip()
    if trimmed.startswith('{') and trimmed.endswith('}'):
        try:
            json.loads(trimmed)
            return trimmed
        except json.JSONDecodeError:
            pass

    lowered = candidate.lower()
    anchors: list[int] = []
    key_anchor = lowered.find('"visual_type"')
    if key_anchor != -1:
        brace_before_key = candidate.rfind('{', 0, key_anchor)
        if brace_before_key != -1:
            anchors.append(brace_before_key)
    first_brace = candidate.find('{')
    if first_brace != -1:
        anchors.append(first_brace)

    for anchor in anchors:
        extracted = _extract_object(candidate, anchor)
        if extracted:
            candidate_obj = extracted.strip()
            try:
                json.loads(candidate_obj)
                return candidate_obj
            except json.JSONDecodeError:
                continue

    return candidate.strip()


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
