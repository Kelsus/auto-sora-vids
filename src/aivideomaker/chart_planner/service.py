from __future__ import annotations

import logging
import re
from typing import Any, Dict, List

from aivideomaker.article_ingest.model import ArticleBundle
from aivideomaker.script_engine.llm import LLMClient
from aivideomaker.script_engine.utils import load_json_with_repair

from .models import ChartDataPoint, ChartIdea, ChartPlan
from .prompts import render_chart_analysis_prompt

logger = logging.getLogger(__name__)

_SLUG_PATTERN = re.compile(r"[^a-z0-9-]+")


class ChartPlanner:
    def __init__(self, llm: LLMClient, *, max_charts: int = 2, excerpt_chars: int = 2600) -> None:
        self.llm = llm
        self.max_charts = max_charts
        self.excerpt_chars = excerpt_chars

    def analyze_article(self, bundle: ArticleBundle) -> ChartPlan:
        prompt = render_chart_analysis_prompt(
            bundle,
            max_charts=self.max_charts,
            excerpt_chars=self.excerpt_chars,
        )
        raw = self.llm.complete(prompt)
        payload = load_json_with_repair(raw, logger=logger)
        charts_payload = payload.get("charts") if isinstance(payload, dict) else None
        if not charts_payload:
            return ChartPlan(charts=[])

        charts: List[ChartIdea] = []
        for chart_dict in charts_payload[: self.max_charts]:
            try:
                idea = self._build_chart_idea(chart_dict)
            except Exception as exc:  # pragma: no cover - defensive parsing guard
                logger.warning("Discarding malformed chart payload: %s", exc, exc_info=True)
                continue
            if not idea.data_points:
                logger.debug("Skipping chart %s because it lacks data points", idea.id)
                continue
            # Require at least 3 data points for a chart to be meaningful
            if len(idea.data_points) < 3:
                logger.debug(
                    "Skipping chart %s because it has only %d data points (minimum 3 required)",
                    idea.id,
                    len(idea.data_points),
                )
                continue
            charts.append(idea)

        return ChartPlan(charts=charts)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _build_chart_idea(self, payload: Dict[str, Any]) -> ChartIdea:
        slug = payload.get("id") or payload.get("slug") or payload.get("title", "chart")
        normalized_id = self._slugify(slug)

        keywords = payload.get("keywords") or []
        keywords = [self._clean_keyword(token) for token in keywords if token]

        data_points_payload = payload.get("data_points") or []
        data_points = [self._build_data_point(item) for item in data_points_payload if item]

        return ChartIdea(
            id=normalized_id,
            title=str(payload.get("title") or "Untitled chart").strip(),
            summary=str(payload.get("summary") or "").strip() or "No summary provided",
            reason=str(payload.get("reason") or "").strip() or "",
            variant=self._sanitize_optional(payload.get("variant")),
            subtitle=self._sanitize_optional(payload.get("subtitle")),
            note=self._sanitize_optional(payload.get("note")),
            x_label=self._sanitize_optional(payload.get("x_label")),
            y_label=self._sanitize_optional(payload.get("y_label")),
            source=self._sanitize_optional(payload.get("source")),
            keywords=keywords,
            data_points=data_points,
            code_prompt=self._sanitize_optional(payload.get("code_prompt")),
        )

    def _build_data_point(self, payload: Dict[str, Any]) -> ChartDataPoint:
        label = str(payload.get("label") or payload.get("category") or payload.get("time") or "").strip()
        if not label:
            raise ValueError("Chart data point missing label")
        value = payload.get("value")
        if value is None:
            raise ValueError("Chart data point missing value")
        secondary = payload.get("secondary_value")
        try:
            primary_value = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid primary value for chart point '{label}': {value}") from exc
        secondary_value = None
        if secondary is not None:
            try:
                secondary_value = float(secondary)
            except (TypeError, ValueError):
                secondary_value = None
        series_raw = payload.get("series") or payload.get("group")
        series = str(series_raw).strip() if series_raw else None
        return ChartDataPoint(
            label=label,
            value=primary_value,
            secondary_value=secondary_value,
            series=series if series else None,
        )

    def _slugify(self, value: str) -> str:
        candidate = value.strip().lower().replace(" ", "-")
        candidate = _SLUG_PATTERN.sub("-", candidate)
        candidate = candidate.strip("-") or "chart"
        if len(candidate) > 60:
            candidate = candidate[:60].rstrip("-")
        return candidate or "chart"

    def _sanitize_optional(self, value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    def _clean_keyword(self, token: str) -> str:
        return self._slugify(token).replace("-", " ")


__all__ = ["ChartPlanner"]
