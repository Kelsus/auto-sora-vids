from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List
import textwrap

from moviepy.audio.fx import all as afx
from moviepy.editor import (
    AudioFileClip,
    VideoFileClip,
    concatenate_videoclips,
    CompositeAudioClip,
    TextClip,
    CompositeVideoClip,
)

logger = logging.getLogger(__name__)


@dataclass
class CaptionSegment:
    start: float
    end: float
    text: str


class Stitcher:
    def __init__(self, export_dir: Path) -> None:
        self._export_dir = Path(export_dir)

    @property
    def export_dir(self) -> Path:
        return self._export_dir

    @export_dir.setter
    def export_dir(self, value: Path) -> None:
        self._export_dir = Path(value)

    def stitch(
        self,
        video_paths: Iterable[Path],
        voice_track: Path | None = None,
        music_track: Path | None = None,
        captions: List[CaptionSegment] | None = None,
        *,
        voice_volume: float = 1.0,
        music_volume: float = 0.12,
        level_audio: bool = True,
        output_basename: str = "final_video",
    ) -> Path:
        self.export_dir.mkdir(parents=True, exist_ok=True)
        clips = [VideoFileClip(str(path)) for path in video_paths]
        if not clips:
            raise ValueError("No clips supplied for stitching")
        final = concatenate_videoclips(clips, method="compose")

        audio_clips = []
        voice_clip = None
        music_clip = None
        target_duration = final.duration

        if voice_track:
            voice_clip = AudioFileClip(str(voice_track)).volumex(voice_volume)
            audio_clips.append(voice_clip)
            target_duration = voice_clip.duration

        if music_track:
            music_clip = AudioFileClip(str(music_track)).volumex(music_volume)
            loop_target = target_duration if voice_track else final.duration
            music_clip = afx.audio_loop(music_clip, duration=loop_target)
            audio_clips.append(music_clip)

        if audio_clips:
            composite = CompositeAudioClip(audio_clips)
            final = final.set_audio(composite)
        else:
            composite = None

        if captions:
            caption_clips = self._build_caption_clips(final, captions)
            if caption_clips:
                captioned = CompositeVideoClip([final] + caption_clips)
                captioned = captioned.set_audio(final.audio)
                final = captioned

        if voice_track:
            final = final.subclip(0, target_duration)

        if level_audio:
            logger.info("Audio leveling placeholder; integrate ffmpeg filters here")

        raw_base = output_basename.strip() or "final_video"
        safe_base = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in raw_base)
        if not safe_base:
            safe_base = "final_video"
        output_path = self.export_dir / f"{safe_base}.mp4"
        final.write_videofile(
            str(output_path),
            codec="libx264",
            audio_codec="aac",
            temp_audiofile=str(output_path.with_suffix('.temp-audio.m4a')),
            remove_temp=True,
        )
        for clip in clips:
            clip.close()
        if voice_track:
            voice_clip.close()
        if music_clip:
            music_clip.close()
        if composite:
            composite.close()
        final.close()
        return output_path

    def _build_caption_clips(
        self, base_clip: VideoFileClip, captions: List[CaptionSegment]
    ) -> List[TextClip]:
        clips: List[TextClip] = []
        font_size = int(base_clip.h * 0.045)
        box_width = int(base_clip.w * 0.9)
        for segment in captions:
            duration = max(segment.end - segment.start, 0.5)
            text = self._wrap_caption_text(segment.text)
            if not text:
                continue
            base_txt = TextClip(
                text,
                method="caption",
                fontsize=font_size,
                font="Helvetica",
                color="white",
                align="center",
                size=(box_width, None),
            )
            txt = base_txt.on_color(
                color=(0, 0, 0),
                col_opacity=0.65,
                size=(box_width, base_txt.h + 1),
            ).margin(left=20, right=20, top=15, bottom=30, opacity=0)
            txt = txt.set_position(("center", base_clip.h * 0.78))
            txt = txt.set_start(segment.start).set_duration(duration)
            clips.append(txt)
        return clips

    @staticmethod
    def _wrap_caption_text(text: str, width: int = 38) -> str:
        cleaned = " ".join(text.strip().split())
        if not cleaned:
            return ""
        lines = textwrap.wrap(cleaned, width=width)
        return "\n".join(lines)
