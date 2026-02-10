from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Iterable, Iterator, List, Sequence

from aivideomaker.script_engine.model import ScriptPlan

from .model import Chunk, ChunkPlan

logger = logging.getLogger(__name__)

WORD_PATTERN = re.compile(r"\S+")
MIN_CHUNK_DURATION = 3.0
MAX_CHUNK_DURATION = 12.0
DURATION_PADDING = 0.35
PREFERRED_MID_DURATION = 8.0


@dataclass
class WordTiming:
    text: str
    start: float
    end: float


class ChunkPlanner:
    """Translate narration timelines into Veo-friendly chunks."""

    def plan(
        self,
        script: ScriptPlan,
        alignment: dict | None = None,
        one_clip_per_beat: bool = False,
    ) -> ChunkPlan:
        if one_clip_per_beat:
            return self._plan_one_per_beat(script, alignment)
        if alignment:
            try:
                return self._plan_with_alignment(script, alignment)
            except Exception as exc:  # pragma: no cover - defensive fallback
                logger.warning("Alignment-driven planning failed; falling back to heuristic split: %s", exc)
        return self._plan_without_alignment(script)

    # One clip per beat (e.g. for consistent-character with fixed clip duration) --

    def _plan_one_per_beat(self, script: ScriptPlan, alignment: dict | None) -> ChunkPlan:
        words: list[WordTiming] | None = None
        if alignment:
            try:
                words = self._parse_alignment(script.full_transcript, alignment)
            except Exception:
                logger.warning("Alignment parsing failed in one_clip_per_beat; using heuristic durations")

        word_iter = iter(words) if words else None
        chunks: list[Chunk] = []
        cursor = 0.0

        for beat in script.beats:
            text = beat.transcript.strip()
            if word_iter:
                beat_words = list(self._consume_words_for_text(word_iter, text))
                if beat_words:
                    start = beat_words[0].start
                    end = beat_words[-1].end
                    duration = float(self._apply_padding(end - start))
                else:
                    start = cursor
                    duration = max(len(text.split()) / 2.5, MIN_CHUNK_DURATION)
                    end = start + duration
            else:
                start = cursor
                duration = max(len(text.split()) / 2.5, MIN_CHUNK_DURATION)
                duration = float(self._apply_padding(duration))
                end = start + duration

            chunks.append(
                Chunk(
                    id=beat.id,
                    beat_id=beat.id,
                    transcript=text,
                    estimated_duration_sec=duration,
                    start_time_sec=start,
                    end_time_sec=end,
                )
            )
            cursor = end

        total = sum(c.estimated_duration_sec for c in chunks)
        return ChunkPlan(chunks=chunks, total_duration_sec=total)

    # Alignment-aware planning -------------------------------------------------

    def _plan_with_alignment(self, script: ScriptPlan, alignment: dict) -> ChunkPlan:
        words = self._parse_alignment(script.full_transcript, alignment)
        word_iter = iter(words)
        chunks: list[Chunk] = []

        for beat in script.beats:
            beat_words = list(self._consume_words_for_text(word_iter, beat.transcript))
            if not beat_words:
                continue
            duration = beat_words[-1].end - beat_words[0].start
            if duration <= MAX_CHUNK_DURATION:
                chunk_id = beat.id
                text = beat.transcript.strip()
                target_duration = self._apply_padding(duration)
                chunks.append(
                    Chunk(
                        id=chunk_id,
                        beat_id=beat.id,
                        transcript=text,
                        estimated_duration_sec=float(target_duration),
                        start_time_sec=float(beat_words[0].start),
                        end_time_sec=float(beat_words[-1].end),
                    )
                )
                continue
            segments = self._segment_beat_words(beat.id, beat.transcript, beat_words)
            chunks.extend(segments)

        total = sum(chunk.estimated_duration_sec for chunk in chunks)
        return ChunkPlan(chunks=chunks, total_duration_sec=total)

    def _parse_alignment(self, transcript: str, alignment: dict) -> list[WordTiming]:
        payload = alignment.get("alignment") or alignment
        chars: Sequence[str] = payload.get("characters", [])
        starts: Sequence[float] = payload.get("character_start_times_seconds", [])
        ends: Sequence[float] = payload.get("character_end_times_seconds", [])
        if not (chars and starts and ends) or not (len(chars) == len(starts) == len(ends)):
            raise ValueError("Alignment payload missing character timing data")

        mapped: list[tuple[str, float, float]] = []
        align_iter: Iterator[tuple[str, float, float]] = iter(zip(chars, starts, ends))

        for char in transcript:
            mapped_char, start, end = self._next_matching_char(char, align_iter)
            mapped.append((mapped_char, start, end))

        words: list[WordTiming] = []
        current_chars: list[str] = []
        current_start: float | None = None
        current_end: float | None = None

        for char, start, end in mapped:
            if char.isspace():
                if current_chars:
                    text = "".join(current_chars)
                    words.append(WordTiming(text=text, start=current_start or start, end=current_end or end))
                    current_chars = []
                    current_start = None
                    current_end = None
                continue

            if current_start is None:
                current_start = start
            current_chars.append(char)
            current_end = end

        if current_chars:
            text = "".join(current_chars)
            words.append(WordTiming(text=text, start=current_start or 0.0, end=current_end or current_start or 0.0))

        return words

    def _next_matching_char(
        self,
        target: str,
        align_iter: Iterator[tuple[str, float, float]],
    ) -> tuple[str, float, float]:
        """Advance through alignment chars until we find a match for the transcript char."""

        lowered_target = target.lower()
        while True:
            mapped_char, start, end = next(align_iter)
            if mapped_char == target:
                return mapped_char, start, end
            if mapped_char.isspace() and target.isspace():
                return target, start, end
            if mapped_char.lower() == lowered_target:
                return target, start, end
            # Otherwise skip extra alignment char and continue.

    def _consume_words_for_text(
        self,
        word_iter: Iterator[WordTiming],
        text: str,
    ) -> Iterator[WordTiming]:
        expected_words = WORD_PATTERN.findall(text)
        for _ in expected_words:
            try:
                yield next(word_iter)
            except StopIteration:
                logger.warning("Ran out of alignment words while mapping beat text")
                return

    def _segment_beat_words(
        self,
        beat_id: str,
        beat_text: str,
        words: list[WordTiming],
    ) -> list[Chunk]:
        segments: list[Chunk] = []
        current: list[WordTiming] = []
        seg_index = 1

        def flush() -> None:
            nonlocal current, seg_index
            if not current:
                return
            start = current[0].start
            end = current[-1].end
            actual_duration = max(end - start, 0.01)
            target_duration = self._apply_padding(actual_duration)
            text = self._compose_segment_text(current)
            chunk_id = beat_id if len(words) == len(current) else f"{beat_id}-{seg_index}"
            segments.append(
                Chunk(
                    id=chunk_id,
                    beat_id=beat_id,
                    transcript=text,
                    estimated_duration_sec=float(target_duration),
                    start_time_sec=float(start),
                    end_time_sec=float(end),
                )
            )
            seg_index += 1
            current = []

        for idx, word in enumerate(words):
            if not current:
                current.append(word)
                continue

            tentative = current + [word]
            duration = tentative[-1].end - tentative[0].start

            if duration > MAX_CHUNK_DURATION:
                flush()
                current.append(word)
                continue

            current.append(word)
            duration = current[-1].end - current[0].start

            if duration >= MIN_CHUNK_DURATION:
                next_word = words[idx + 1] if idx + 1 < len(words) else None
                if duration >= PREFERRED_MID_DURATION or not next_word:
                    flush()
                else:
                    projected = next_word.end - current[0].start
                    if projected > MAX_CHUNK_DURATION:
                        flush()

        flush()
        return segments

    def _compose_segment_text(self, words: list[WordTiming]) -> str:
        text = " ".join(w.text for w in words)
        text = re.sub(r"\s+([.,!?;:])", r"\1", text)
        return text.strip()

    # Heuristic fallback -------------------------------------------------------

    def _plan_without_alignment(self, script: ScriptPlan) -> ChunkPlan:
        chunks: list[Chunk] = []
        cursor = 0.0
        for beat in script.beats:
            is_chart = bool(getattr(beat.visual, "type", "").lower() == "chart")
            transcripts = [beat.transcript.strip()] if is_chart else self._split_transcript(beat.transcript)
            for index, segment in enumerate(transcripts, start=1):
                chunk_id = beat.id if len(transcripts) == 1 else f"{beat.id}-{index}"
                raw_duration = max(len(segment.split()) / 2.5, MIN_CHUNK_DURATION)
                estimated = float(self._apply_padding(raw_duration))
                start_time = cursor
                end_time = cursor + estimated
                chunks.append(
                    Chunk(
                        id=chunk_id,
                        beat_id=beat.id,
                        transcript=segment,
                        estimated_duration_sec=estimated,
                        start_time_sec=float(start_time),
                        end_time_sec=float(end_time),
                    )
                )
                cursor = end_time
        total = sum(chunk.estimated_duration_sec for chunk in chunks)
        return ChunkPlan(chunks=chunks, total_duration_sec=total)

    def _split_transcript(self, transcript: str) -> list[str]:
        sentences = re.split(r"(?<=[.!?])\s+", transcript.strip())
        return [sentence for sentence in sentences if sentence]

    @staticmethod
    def batch(chunks: Iterable[Chunk], batch_duration: float = MAX_CHUNK_DURATION) -> list[list[Chunk]]:
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

    @staticmethod
    def _apply_padding(seconds: float) -> float:
        seconds = max(seconds, 0.01)
        padded = seconds + DURATION_PADDING
        clipped = max(padded, MIN_CHUNK_DURATION)
        clipped = min(clipped, MAX_CHUNK_DURATION)
        return round(clipped, 2)
