from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from aivideomaker.article_ingest.model import ArticleBundle

from .llm import EchoLLM, LLMClient
from .model import ScriptPlan
from .prompts import render_review_prompt
from .utils import load_json_with_repair

logger = logging.getLogger(__name__)


class ScriptReviewDecision(BaseModel):
    """Represents the automated reviewer judgement on a script plan."""

    verdict: str = Field(
        description="Overall decision. Expected values: approve or revise.",
    )
    summary: str = Field(default="", description="Short summary of the review outcome.")
    strengths: list[str] = Field(default_factory=list, description="Positive call-outs.")
    concerns: list[str] = Field(
        default_factory=list,
        description="Blocking issues or risks that must be resolved before proceeding.",
    )
    action_items: list[str] = Field(
        default_factory=list,
        description="Concrete recommendations to fix blocking issues.",
    )

    @property
    def requires_revision(self) -> bool:
        return self.verdict.lower() not in {"approve", "approved", "pass"}


class ScriptReviewer:
    """Runs LLM-powered review checks on generated script plans."""

    def __init__(self, llm: LLMClient | None = None) -> None:
        self.llm = llm or EchoLLM()

    def review(self, article: ArticleBundle, script: ScriptPlan) -> ScriptReviewDecision:
        prompt = render_review_prompt(article, script)
        raw = self.llm.complete(prompt)
        payload = self._parse_response(raw)

        try:
            decision = ScriptReviewDecision.model_validate(payload)
        except ValidationError as exc:
            logger.error("Invalid script review payload: %s", exc)
            raise

        if decision.requires_revision:
            logger.warning("Script did not pass automated review: %s", decision.summary)
        else:
            logger.info("Script passed automated review: %s", decision.summary or "approved")

        return decision

    @staticmethod
    def _parse_response(raw: str) -> dict[str, Any]:
        """Extract JSON from LLM response, attempting repair on failure."""
        return load_json_with_repair(raw, logger=logger, repair_log_level=logging.DEBUG)
