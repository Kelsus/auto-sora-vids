from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Dict, Iterable, List, Sequence

from aivideomaker.script_engine.model import Beat, BeatQCRules, BeatVisualSpec, ScriptPlan

from .models import ChartIdea, ChartPlan

TOKEN_PATTERN = re.compile(r"[a-z0-9']+")

# Minimum score required for a chart to be assigned to a beat.
# This prevents weak/forced chart assignments when keyword overlap is minimal.
MIN_CHART_ASSIGNMENT_SCORE = 4.0


@dataclass(frozen=True)
class ChartAssignment:
    chart_id: str
    beat_id: str


class ChartAssigner:
    def assign(self, plan: ChartPlan, script: ScriptPlan) -> tuple[ScriptPlan, List[ChartAssignment]]:
        if plan.is_empty():
            return script, []

        beats = list(script.beats)
        assignments: List[ChartAssignment] = []
        taken_beats: set[str] = set()

        for chart in plan.charts:
            beat_id = self._select_best_beat(chart, beats, taken_beats)
            if not beat_id:
                continue
            taken_beats.add(beat_id)
            assignments.append(ChartAssignment(chart_id=chart.id, beat_id=beat_id))

        if not assignments:
            return script, []

        chart_map: Dict[str, ChartIdea] = {assignment.beat_id: self._find_chart(plan, assignment.chart_id) for assignment in assignments}

        updated_beats = [self._apply_chart(beat, chart_map.get(beat.id)) for beat in beats]
        updated_script = script.model_copy(update={"beats": updated_beats})
        return updated_script, assignments

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _select_best_beat(
        self,
        chart: ChartIdea,
        beats: Sequence[Beat],
        taken: set[str],
    ) -> str | None:
        keywords = chart.keyword_set()
        if not keywords:
            keywords = self._infer_keywords(chart)

        best_score = -math.inf
        best_id: str | None = None
        for beat in beats:
            if beat.id in taken:
                continue
            score = self._score_chart_fit(chart, keywords, beat)
            if score > best_score:
                best_score = score
                best_id = beat.id

        # Reject assignment if the best score is below threshold - prevents forced/weak assignments
        if best_score < MIN_CHART_ASSIGNMENT_SCORE:
            return None

        return best_id

    def _score_chart_fit(self, chart: ChartIdea, keywords: set[str], beat: Beat) -> float:
        transcript_tokens = self._token_set(beat.transcript)
        purpose_tokens = self._token_set(beat.purpose)
        combined = transcript_tokens | purpose_tokens

        keyword_hits = sum(1 for token in keywords if token in combined)
        if chart.source:
            source_tokens = self._token_set(chart.source)
            keyword_hits += sum(0.5 for token in source_tokens if token in combined)

        summary_tokens = self._token_set(chart.summary)
        overlap = len(combined & summary_tokens)

        # Reward beats that mention numbers since charts often rely on data narration.
        numeric_hint = 1.5 if any(char.isdigit() for char in beat.transcript) else 0.0

        return keyword_hits * 3.0 + overlap + numeric_hint

    def _apply_chart(self, beat: Beat, chart: ChartIdea | None) -> Beat:
        if not chart:
            return beat

        visual = beat.visual or BeatVisualSpec(type="chart")
        updated_visual = visual.model_copy(
            update={
                "type": "chart",
                "spec_id": chart.id,
                "chart_variant": chart.variant,
                "chart_reason": chart.reason or chart.summary,
                "chart_data_available": True,
                "chart_should_render": True,
                "chart_title": chart.title,
                "chart_subtitle": chart.subtitle,
                "chart_x_label": chart.x_label,
                "chart_y_label": chart.y_label,
                "chart_note": chart.note,
                "chart_series": [
                    {
                        "label": point.label,
                        "value": point.value,
                        "secondary_value": point.secondary_value,
                        "series": point.series,
                    }
                    for point in chart.data_points
                ],
            }
        )

        qc = beat.qc or BeatQCRules()
        qc_update = qc.model_copy(update={"allow_numbers": True})

        return beat.model_copy(update={"visual": updated_visual, "qc": qc_update})

    def _infer_keywords(self, chart: ChartIdea) -> set[str]:
        inferred = set()
        inferred.update(self._token_set(chart.title))
        inferred.update(self._token_set(chart.summary))
        inferred.update(self._token_set(chart.reason))
        return {token for token in inferred if len(token) > 2}

    def _token_set(self, text: str | None) -> set[str]:
        if not text:
            return set()
        return {match.group(0) for match in TOKEN_PATTERN.finditer(text.lower())}

    def _find_chart(self, plan: ChartPlan, chart_id: str) -> ChartIdea:
        for chart in plan.charts:
            if chart.id == chart_id:
                return chart
        raise KeyError(f"Chart {chart_id} not found in plan")


__all__ = ["ChartAssigner", "ChartAssignment"]
