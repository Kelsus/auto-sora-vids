from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Iterable, List, Sequence

from aivideomaker.script_engine.model import Beat, BeatVisualSpec, ScriptPlan

from .models import UserImageAsset, UserImagePlan

TOKEN_PATTERN = re.compile(r"[a-z0-9']+")


@dataclass(frozen=True)
class UserImageAssignment:
    image_id: str
    beat_id: str


class UserImageAssigner:
    def assign(
        self,
        plan: UserImagePlan,
        script: ScriptPlan,
        *,
        blocked_beats: Iterable[str] = (),
    ) -> tuple[ScriptPlan, List[UserImageAssignment]]:
        if plan.is_empty():
            return script, []

        beats = list(script.beats)
        taken_beats: set[str] = set(blocked_beats)
        assignments: list[UserImageAssignment] = []

        for image in plan.images:
            beat_id = self._select_best_beat(image, beats, taken_beats)
            if not beat_id:
                continue
            taken_beats.add(beat_id)
            assignments.append(UserImageAssignment(image_id=image.id, beat_id=beat_id))

        if not assignments:
            return script, []

        assignments_by_beat = {assignment.beat_id: assignment.image_id for assignment in assignments}
        updated_beats = [
            self._apply_user_image(beat, assignments_by_beat.get(beat.id))
            for beat in beats
        ]
        return script.model_copy(update={"beats": updated_beats}), assignments

    def _select_best_beat(
        self,
        image: UserImageAsset,
        beats: Sequence[Beat],
        taken: set[str],
    ) -> str | None:
        keywords = self._token_set(" ".join(image.keywords or [])) | self._token_set(image.title) | self._token_set(image.summary)
        keywords = {token for token in keywords if len(token) > 2}
        if not keywords:
            return None

        best_score = -math.inf
        best_id: str | None = None

        for beat in beats:
            if beat.id in taken:
                continue
            score = self._score_fit(keywords, beat)
            if score > best_score:
                best_score = score
                best_id = beat.id

        return best_id

    def _score_fit(self, keywords: set[str], beat: Beat) -> float:
        transcript_tokens = self._token_set(beat.transcript)
        purpose_tokens = self._token_set(beat.purpose)
        combined = transcript_tokens | purpose_tokens
        keyword_hits = sum(1 for token in keywords if token in combined)
        return keyword_hits * 3.0 + len(combined & keywords) * 0.5

    def _apply_user_image(self, beat: Beat, image_id: str | None) -> Beat:
        if not image_id:
            return beat

        existing = beat.visual
        if existing is None:
            visual = BeatVisualSpec(type="still_motion")
        else:
            visual = existing

        updated_visual = visual.model_copy(
            update={
                "type": "still_motion",
                "macro": None,
                "spec_id": None,
                "chart_variant": None,
                "chart_reason": None,
                "chart_data_available": None,
                "chart_should_render": None,
                "chart_duplicates_previous": None,
                "chart_title": None,
                "chart_subtitle": None,
                "chart_x_label": None,
                "chart_y_label": None,
                "chart_note": None,
                "chart_series": None,
            }
        )
        return beat.model_copy(update={"visual": updated_visual})

    def _token_set(self, text: str | None) -> set[str]:
        if not text:
            return set()
        return {match.group(0) for match in TOKEN_PATTERN.finditer(text.lower())}


__all__ = ["UserImageAssigner", "UserImageAssignment"]

