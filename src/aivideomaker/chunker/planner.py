from __future__ import annotations

import re
from typing import Iterable, List

from aivideomaker.script_engine.model import ScriptPlan

from .model import Chunk, ChunkPlan

WORDS_PER_SECOND = 2.5  # conversational pacing
# Veo hard limit is 8 seconds per clip; keep chunks within that window.
MAX_CHUNK_DURATION = 8.0
MAX_WORDS_PER_CHUNK = int(WORDS_PER_SECOND * MAX_CHUNK_DURATION)


def estimate_duration(text: str) -> float:
    words = max(len(text.split()), 1)
    return words / WORDS_PER_SECOND


def enforce_limits(duration: float) -> float:
    return min(duration, MAX_CHUNK_DURATION)


class ChunkPlanner:
    """Translate script beats into Sora-sized chunks."""

    def plan(self, script: ScriptPlan) -> ChunkPlan:
        chunks: list[Chunk] = []
        for beat in script.beats:
            transcripts = self._split_transcript(beat.transcript)
            for index, segment in enumerate(transcripts, start=1):
                chunk_id = beat.id if len(transcripts) == 1 else f"{beat.id}-{index}"
                chunks.append(self._build_chunk(chunk_id=chunk_id, beat_id=beat.id, transcript=segment))
        total = sum(chunk.estimated_duration_sec for chunk in chunks)
        return ChunkPlan(chunks=chunks, total_duration_sec=total)

    def _build_chunk(self, chunk_id: str, beat_id: str, transcript: str) -> Chunk:
        duration = enforce_limits(estimate_duration(transcript))
        return Chunk(id=chunk_id, beat_id=beat_id, transcript=transcript, estimated_duration_sec=duration)

    def _split_transcript(self, transcript: str) -> list[str]:
        words = transcript.split()
        if len(words) <= MAX_WORDS_PER_CHUNK:
            return [transcript.strip()]

        segments: list[str] = []
        current: list[str] = []
        current_word_count = 0

        # Pre-split on sentence boundaries to keep narration natural.
        sentences = re.split(r"(?<=[.!?])\s+", transcript.strip())
        for sentence in sentences:
            sentence_words = sentence.split()
            if not sentence_words:
                continue
            if len(sentence_words) > MAX_WORDS_PER_CHUNK:
                # Fall back to word-level chunks for long sentences.
                segments.extend(self._chunk_by_word(sentence_words))
                current = []
                current_word_count = 0
                continue

            if current_word_count + len(sentence_words) > MAX_WORDS_PER_CHUNK and current:
                segments.append(" ".join(current).strip())
                current = []
                current_word_count = 0

            current.extend(sentence_words)
            current_word_count += len(sentence_words)

        if current:
            segments.append(" ".join(current).strip())

        # Guard against edge cases where no segments were produced.
        return [segment for segment in segments if segment] or [transcript.strip()]

    def _chunk_by_word(self, words: List[str]) -> list[str]:
        segments: list[str] = []
        current: list[str] = []
        for word in words:
            current.append(word)
            if len(current) >= MAX_WORDS_PER_CHUNK:
                segments.append(" ".join(current).strip())
                current = []
        if current:
            segments.append(" ".join(current).strip())
        return segments

    @staticmethod
    def batch(chunks: Iterable[Chunk], batch_duration: float = MAX_CHUNK_DURATION) -> list[list[Chunk]]:
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
