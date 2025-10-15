from __future__ import annotations

import logging

from typing import TYPE_CHECKING
from pydantic import ValidationError

from aivideomaker.article_ingest.model import ArticleBundle

from .llm import EchoLLM, LLMClient
from .model import ScriptPlan
from .prompts import render_planning_prompt

if TYPE_CHECKING:
    from .reviewer import ScriptReviewDecision
from .utils import load_json_with_repair

logger = logging.getLogger(__name__)


class ScriptEngine:
    def __init__(self, llm: LLMClient | None = None) -> None:
        self.llm = llm or EchoLLM()

    def generate_script(
        self,
        article: ArticleBundle,
        review: "ScriptReviewDecision | None" = None,
        previous_script: ScriptPlan | None = None,
    ) -> ScriptPlan:
        prompt = render_planning_prompt(article, review=review, previous_script=previous_script)
        raw = self.llm.complete(prompt)
        logger.debug("LLM raw response: %s", raw)
        payload = load_json_with_repair(raw, logger=logger)

        try:
            return ScriptPlan.model_validate(payload)
        except ValidationError as exc:
            logger.error("Invalid script plan payload: %s", exc)
            raise
