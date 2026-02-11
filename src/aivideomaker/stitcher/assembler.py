from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Tuple

from moviepy.audio.fx import all as afx
from moviepy.editor import (
    AudioFileClip,
    VideoFileClip,
    concatenate_videoclips,
    CompositeAudioClip,
)
import ffmpeg

from PIL import Image

# moviepy still references the deprecated Image.ANTIALIAS constant on older
# releases. Pillow 10+ removed it, so provide a compatibility alias.
if not hasattr(Image, "ANTIALIAS") and hasattr(Image, "Resampling"):  # pragma: no cover
    Image.ANTIALIAS = Image.Resampling.LANCZOS  # type: ignore[attr-defined]

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
        captions_ass: Path | None = None,
        *,
        voice_volume: float = 1.0,
        music_volume: float = 0.12,
        level_audio: bool = True,
        output_basename: str = "final_video",
        keep_video_audio: bool = False,
    ) -> Path:
        self.export_dir.mkdir(parents=True, exist_ok=True)
        clips = [VideoFileClip(str(path)) for path in video_paths]
        if not clips:
            raise ValueError("No clips supplied for stitching")
        # Ensure all clips share a common frame size so we don't introduce padding.
        clip_sizes: Counter[Tuple[int, int]] = Counter(
            (int(clip.w), int(clip.h)) for clip in clips
        )

        target_size: Tuple[int, int]
        # Pick the most common resolution; this avoids shrinking dominant clips if
        # isolated assets (e.g., charts/stills) differ slightly.
        target_size = max(clip_sizes.items(), key=lambda item: (item[1], item[0]))[0]
        needs_normalization = len(clip_sizes) > 1

        resized_wrappers: list = []
        concat_clips = clips if not needs_normalization else []

        if needs_normalization:
            logger.info(
                "Normalizing clip resolutions for stitching: target=%sx%s, variants=%s",
                target_size[0],
                target_size[1],
                sorted({f"{w}x{h}" for (w, h) in clip_sizes}),
            )
            for clip in clips:
                clip_size = (int(clip.w), int(clip.h))
                if clip_size == target_size:
                    concat_clips.append(clip)
                    continue
                resized = clip.resize(newsize=target_size)
                concat_clips.append(resized)
                resized_wrappers.append(resized)

        final = concatenate_videoclips(concat_clips, method="compose")

        audio_clips = []
        voice_clip = None
        music_clip = None
        video_duration = final.duration
        target_duration = video_duration
        duration_tolerance = 0.05

        if keep_video_audio and final.audio is not None:
            audio_clips.append(final.audio)

        if voice_track and not keep_video_audio:
            voice_clip = AudioFileClip(str(voice_track)).volumex(voice_volume)
            voice_duration = voice_clip.duration
            audio_clips.append(voice_clip)
            # Set target to voice duration to ensure video matches narration length
            target_duration = voice_duration

        if music_track:
            effective_music_volume = music_volume * 0.5 if keep_video_audio else music_volume
            music_clip = AudioFileClip(str(music_track)).volumex(effective_music_volume)
            loop_target = target_duration
            music_clip = afx.audio_loop(music_clip, duration=loop_target)
            audio_clips.append(music_clip)

        if audio_clips:
            composite = CompositeAudioClip(audio_clips)
            final = final.set_audio(composite)
        else:
            composite = None

        # Synchronize video duration with narration (skip when keeping video audio)
        if not keep_video_audio:
            if target_duration > video_duration + duration_tolerance:
                logger.info(
                    "Extending final video from %.2fs to %.2fs to align with narration",
                    video_duration,
                    target_duration,
                )
                final = final.set_duration(target_duration)
            elif video_duration > target_duration + duration_tolerance:
                logger.info(
                    "Trimming final video from %.2fs to %.2fs to align with narration",
                    video_duration,
                    target_duration,
                )
                final = final.subclip(0, target_duration)

        if level_audio:
            logger.info("Audio leveling placeholder; integrate ffmpeg filters here")

        raw_base = output_basename.strip() or "final_video"
        safe_base = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in raw_base)
        if not safe_base:
            safe_base = "final_video"
        output_path = self.export_dir / f"{safe_base}.mp4"

        has_audio = final.audio is not None

        # If burning ASS captions, first render a temp file, then overlay via ffmpeg libass
        if captions_ass is not None:
            temp_path = self.export_dir / f"{safe_base}.precap.mp4"
            final.write_videofile(
                str(temp_path),
                codec="libx264",
                audio_codec="aac",
                temp_audiofile=str(temp_path.with_suffix('.temp-audio.m4a')),
                remove_temp=True,
            )
            # Use ffmpeg subtitles filter (libass) to burn captions with optional fontsdir
            input_stream = ffmpeg.input(str(temp_path))
            fonts_dir = self.export_dir / "fonts"
            if fonts_dir.exists() and fonts_dir.is_dir():
                subbed_video = input_stream.video.filter(
                    'subtitles',
                    str(captions_ass),
                    fontsdir=str(fonts_dir),
                )
            else:
                subbed_video = input_stream.video.filter('subtitles', str(captions_ass))

            output_args = [subbed_video]
            output_kwargs = {
                'vcodec': 'libx264',
                'crf': 18,
                'preset': 'slow',
                'movflags': '+faststart',
            }
            if has_audio:
                output_args.append(input_stream.audio)
                output_kwargs['c:a'] = 'copy'

            (
                ffmpeg
                .output(
                    *output_args,
                    str(output_path),
                    **output_kwargs,
                )
                .overwrite_output()
                .run(quiet=False)
            )
            try:
                temp_path.unlink()
            except Exception:
                pass
        else:
            final.write_videofile(
                str(output_path),
                codec="libx264",
                audio_codec="aac",
                temp_audiofile=str(output_path.with_suffix('.temp-audio.m4a')),
                remove_temp=True,
            )
        for wrapper in resized_wrappers:
            wrapper.close()
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
