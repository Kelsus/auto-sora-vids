from __future__ import annotations

import json
import logging
from typing import Any

from json_repair import repair_json
from pydantic import ValidationError

from aivideomaker.article_ingest.model import ArticleBundle

from .llm import LLMClient, EchoLLM
from .model import ScriptPlan
from .prompts import render_planning_prompt

logger = logging.getLogger(__name__)


class ScriptEngine:
    def __init__(self, llm: LLMClient | None = None) -> None:
        self.llm = llm or EchoLLM()

    def generate_script(self, article: ArticleBundle) -> ScriptPlan:
        prompt = render_planning_prompt(article)
        raw = self.llm.complete(prompt)
        logger.debug("LLM raw response: %s", raw)
        cleaned = _extract_json_block(raw)

        try:
            payload = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            logger.warning("Primary JSON parse failed, attempting repair: %s", exc)
            try:
                repaired = repair_json(cleaned)
                payload = json.loads(repaired)
            except Exception as repair_exc:
                logger.error("JSON repair failed: %s", repair_exc)
                raise exc from repair_exc

        try:
            return ScriptPlan.model_validate(payload)
        except ValidationError as exc:
            logger.error("Invalid script plan payload: %s", exc)
            raise


def _extract_json_block(text: str) -> str:
    candidate = text.strip()
    if candidate.startswith("```"):
        lines = candidate.splitlines()
        if len(lines) >= 2 and lines[0].startswith("```"):
            candidate = "\n".join(lines[1:-1] if lines[-1].startswith("```") else lines[1:])
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start != -1 and end != -1 and end >= start:
        return candidate[start : end + 1]
    return candidate
