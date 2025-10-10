from __future__ import annotations

import math
from typing import Iterable

from aivideomaker.script_engine.model import ScriptPlan

from .model import Chunk, ChunkPlan

WORDS_PER_SECOND = 2.5  # conversational pacing
SORA_MAX_DURATION = 60.0


def estimate_duration(text: str) -> float:
    words = max(len(text.split()), 1)
    return words / WORDS_PER_SECOND


def enforce_limits(duration: float) -> float:
    return min(duration, SORA_MAX_DURATION)


class ChunkPlanner:
    """Translate script beats into Sora-sized chunks."""

    def plan(self, script: ScriptPlan) -> ChunkPlan:
        chunks = [self._build_chunk(beat_id=beat.id, transcript=beat.transcript) for beat in script.beats]
        total = sum(chunk.estimated_duration_sec for chunk in chunks)
        return ChunkPlan(chunks=chunks, total_duration_sec=total)

    def _build_chunk(self, beat_id: str, transcript: str) -> Chunk:
        duration = enforce_limits(estimate_duration(transcript))
        return Chunk(beat_id=beat_id, transcript=transcript, estimated_duration_sec=duration)

    @staticmethod
    def batch(chunks: Iterable[Chunk], batch_duration: float = SORA_MAX_DURATION) -> list[list[Chunk]]:
        """Greedy packing helper if we later want to merge beats into fewer clips."""
        current: list[Chunk] = []
        batches: list[list[Chunk]] = []
        current_duration = 0.0
        for chunk in chunks:
            if current and current_duration + chunk.estimated_duration_sec > batch_duration:
                batches.append(current)
                current = []
                current_duration = 0.0
            current.append(chunk)
            current_duration += chunk.estimated_duration_sec
        if current:
            batches.append(current)
        return batches
