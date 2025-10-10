from __future__ import annotations

import json
import logging
from typing import Any

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
        try:
            payload: dict[str, Any] = json.loads(raw)
        except json.JSONDecodeError as exc:
            logger.error("Failed to parse LLM JSON: %s", exc)
            raise
        try:
            return ScriptPlan.model_validate(payload)
        except ValidationError as exc:
            logger.error("Invalid script plan payload: %s", exc)
            raise
