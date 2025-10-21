from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, Sequence

from aivideomaker.chunker.model import ChunkPlan
from aivideomaker.script_engine.model import ScriptPlan


@dataclass
class WordTiming:
    text: str
    start: float
    end: float


def _format_ass_time(seconds: float) -> str:
    # ASS uses h:mm:ss.cs (centiseconds). Clamp at >= 0.
    total_cs = max(0, int(round(seconds * 100)))
    cs = total_cs % 100
    total_seconds = total_cs // 100
    s = total_seconds % 60
    total_minutes = total_seconds // 60
    m = total_minutes % 60
    h = total_minutes // 60
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _next_matching_char(
    target: str,
    align_iter: Iterator[tuple[str, float, float]],
) -> tuple[str, float, float]:
    lowered_target = target.lower()
    while True:
        mapped_char, start, end = next(align_iter)
        if mapped_char == target:
            return mapped_char, start, end
        if mapped_char.isspace() and target.isspace():
            return target, start, end
        if mapped_char.lower() == lowered_target:
            return target, start, end


def _parse_alignment(transcript: str, alignment: dict) -> list[WordTiming]:
    payload = alignment.get("alignment") or alignment
    chars: Sequence[str] = payload.get("characters", [])
    starts: Sequence[float] = payload.get("character_start_times_seconds", [])
    ends: Sequence[float] = payload.get("character_end_times_seconds", [])
    if not (chars and starts and ends) or not (len(chars) == len(starts) == len(ends)):
        raise ValueError("Alignment payload missing character timing data")

    mapped: list[tuple[str, float, float]] = []
    align_iter: Iterator[tuple[str, float, float]] = iter(zip(chars, starts, ends))

    for ch in transcript:
        mapped_char, start, end = _next_matching_char(ch, align_iter)
        mapped.append((mapped_char, start, end))

    words: list[WordTiming] = []
    current_chars: list[str] = []
    current_start: float | None = None
    current_end: float | None = None

    for ch, start, end in mapped:
        if ch.isspace():
            if current_chars:
                text = "".join(current_chars)
                words.append(WordTiming(text=text, start=current_start or start, end=current_end or end))
                current_chars = []
                current_start = None
                current_end = None
            continue

        if current_start is None:
            current_start = start
        current_chars.append(ch)
        current_end = end

    if current_chars:
        text = "".join(current_chars)
        words.append(WordTiming(text=text, start=current_start or 0.0, end=current_end or current_start or 0.0))

    return words


def _consume_words_for_text(word_iter: Iterator[WordTiming], text: str) -> Iterable[WordTiming]:
    import re

    pattern = re.compile(r"\S+")
    expected = pattern.findall(text)
    for _ in expected:
        try:
            yield next(word_iter)
        except StopIteration:
            return


def build_karaoke_ass(
    *,
    script: ScriptPlan,
    alignment: dict,
    chunks: ChunkPlan | None = None,
    play_res: tuple[int, int] = (720, 1280),
    style_name: str = "TikTok",
    font: str = "Inter",
    font_size: int = 64,
    outline: int = 3,
    alignment_code: int = 2,  # 2: bottom-center
) -> str:
    # Header and styles (Primary white, Secondary yellow for karaoke fill, Outline black)
    res_x, res_y = play_res
    header = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {res_x}",
        f"PlayResY: {res_y}",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: {style_name}, {font}, {font_size}, &H00FFFFFF, &H0000FFFF, &H00000000, &H64000000, 0,0,0,0, 100,100, 0, 0, 1, {outline}, 0, {alignment_code}, 80,80,120, 1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]

    # Build global word list aligned to the full transcript
    words = _parse_alignment(script.full_transcript, alignment)
    word_iter = iter(words)

    events: list[str] = []

    # Map beat -> its word timings and durations
    for beat in script.beats:
        beat_words = list(_consume_words_for_text(word_iter, beat.transcript))
        if not beat_words:
            continue
        start = beat_words[0].start
        end = beat_words[-1].end
        if end <= start:
            end = start + 0.01

        # Build karaoke payload: {\k<centiseconds>}word with spaces
        parts: list[str] = []
        for idx, w in enumerate(beat_words):
            dur_cs = max(1, int(round((w.end - w.start) * 100)))
            token = w.text
            suffix_space = " " if idx + 1 < len(beat_words) else ""
            parts.append(f"{{\\k{dur_cs}}}{token}{suffix_space}")

        text = "".join(parts)
        line = (
            f"Dialogue: 0,{_format_ass_time(start)},{_format_ass_time(end)},{style_name},,0,0,0,,{text}"
        )
        events.append(line)

    return "\n".join(header + events) + "\n"


def write_karaoke_ass(
    *,
    script: ScriptPlan,
    alignment: dict,
    chunks: ChunkPlan | None,
    export_dir: Path,
    play_res: tuple[int, int] = (720, 1280),
) -> Path:
    export_dir.mkdir(parents=True, exist_ok=True)
    content = build_karaoke_ass(script=script, alignment=alignment, chunks=chunks, play_res=play_res)
    path = export_dir / "captions.ass"
    path.write_text(content, encoding="utf-8")
    return path

