from __future__ import annotations

import logging

from typing import TYPE_CHECKING
from pydantic import ValidationError

from aivideomaker.article_ingest.model import ArticleBundle

from .directives import (
    NarrativeStyleDirective,
    VideoLengthProfile,
    apply_profile_to_script,
    get_length_profile,
    get_style_directive,
)
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
        chart_outline: str | None = None,
        *,
        style_directive: NarrativeStyleDirective | None = None,
        length_profile: VideoLengthProfile | None = None,
    ) -> ScriptPlan:
        directive = style_directive or get_style_directive(None)
        profile = length_profile or get_length_profile(None)
        prompt = render_planning_prompt(
            article,
            review=review,
            previous_script=previous_script,
            chart_outline=chart_outline,
            style_directive=directive,
            length_profile=profile,
        )
        raw = self.llm.complete(prompt)
        logger.debug("LLM raw response: %s", raw)
        payload = load_json_with_repair(raw, logger=logger)

        beats = payload.get("beats") if isinstance(payload, dict) else None
        if isinstance(beats, list):
            for beat in beats:
                if not isinstance(beat, dict):
                    continue
                if "estimated_duration_sec" in beat and beat["estimated_duration_sec"] is not None:
                    continue
                transcript = str(beat.get("transcript") or "")
                word_count = len(transcript.split())
                # Roughly 2.5 words per second; clamp to Sora-friendly window.
                fallback = word_count / 2.5 if word_count else 6.0
                fallback = min(max(fallback, 4.0), 12.0)
                beat["estimated_duration_sec"] = round(fallback, 2)

        try:
            script = ScriptPlan.model_validate(payload)
        except ValidationError as exc:
            logger.error("Invalid script plan payload: %s", exc)
            raise
        return apply_profile_to_script(script, directive, profile)
