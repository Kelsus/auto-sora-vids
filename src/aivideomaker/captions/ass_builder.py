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
    font_size: int = 48,
    outline: int = 3,
    alignment_code: int = 5,  # 5: centered vertically/horizontally
    line_position_ratio: float = 0.58,
    max_chars_per_line: int = 36,
    max_line_duration: float = 3.0,
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
        f"Style: {style_name}, {font}, {font_size}, &H00FFFFFF, &H0000FFFF, &H00000000, &H64000000, 0,0,0,0, 100,100, 0, 0, 1, {outline}, 0, {alignment_code}, 40,40,60, 1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]

    # Build global word list aligned to the full transcript
    words = _parse_alignment(script.full_transcript, alignment)
    word_iter = iter(words)

    events: list[str] = []
    res_x, res_y = play_res
    line_y = int(res_y * line_position_ratio)
    position_prefix = f"{{\\pos({res_x // 2},{line_y})\\q2\\1c&HFFFFFF&}}"

    def append_event(word_slice: list[WordTiming]) -> None:
        if not word_slice:
            return

        segment_start = word_slice[0].start
        segment_end = word_slice[-1].end
        if segment_end <= segment_start:
            segment_end = segment_start + 0.01

        base_parts: list[str] = [position_prefix]
        for idx, w in enumerate(word_slice):
            base_parts.append(w.text)
            if idx + 1 < len(word_slice):
                base_parts.append(" ")
        base_text = "".join(base_parts)
        events.append(
            f"Dialogue: 0,{_format_ass_time(segment_start)},{_format_ass_time(segment_end)},{style_name},,0,0,0,,{base_text}"
        )

        for idx, w in enumerate(word_slice):
            word_start = w.start
            word_end = w.end
            if word_end <= word_start:
                word_end = word_start + 0.01

            highlight_parts: list[str] = [position_prefix, "{\\alpha&HFF&\\1c&HFFFFFF&}"]
            for j, segment_word in enumerate(word_slice):
                if j == idx:
                    highlight_parts.append("{\\alpha&H00&\\1c&H00FFFF&}")
                    highlight_parts.append(segment_word.text)
                    highlight_parts.append("{\\alpha&HFF&\\1c&HFFFFFF&}")
                else:
                    highlight_parts.append(segment_word.text)
                if j + 1 < len(word_slice):
                    highlight_parts.append(" ")

            highlight_text = "".join(highlight_parts)
            events.append(
                f"Dialogue: 0,{_format_ass_time(word_start)},{_format_ass_time(word_end)},{style_name},,0,0,0,,{highlight_text}"
            )

    segment_sources: Iterable[str]
    if chunks and chunks.chunks:
        segment_sources = [chunk.transcript for chunk in chunks.chunks]
    else:
        segment_sources = [beat.transcript for beat in script.beats]

    for segment_text in segment_sources:
        segment_words = list(_consume_words_for_text(word_iter, segment_text))
        if not segment_words:
            continue

        start_idx = 0
        total_words = len(segment_words)
        while start_idx < total_words:
            char_count = 0
            end_idx = start_idx
            base_time = segment_words[start_idx].start
            while end_idx < total_words:
                word = segment_words[end_idx]
                next_chars = len(word.text)
                if end_idx > start_idx:
                    next_chars += 1  # space
                line_duration = word.end - base_time
                over_chars = char_count + next_chars > max_chars_per_line
                over_time = line_duration > max_line_duration
                if (over_chars or over_time) and end_idx > start_idx:
                    break
                char_count += next_chars
                end_idx += 1
            if end_idx == start_idx:
                end_idx += 1
            append_event(segment_words[start_idx:end_idx])
            start_idx = end_idx

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
