from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable

from moviepy.editor import AudioFileClip, VideoFileClip, concatenate_videoclips

logger = logging.getLogger(__name__)


class Stitcher:
    def __init__(self, export_dir: Path) -> None:
        self.export_dir = export_dir
        self.export_dir.mkdir(parents=True, exist_ok=True)

    def stitch(self, video_paths: Iterable[Path], voice_track: Path | None = None, level_audio: bool = True) -> Path:
        clips = [VideoFileClip(str(path)) for path in video_paths]
        if not clips:
            raise ValueError("No clips supplied for stitching")
        final = concatenate_videoclips(clips, method="compose")

        if voice_track:
            voice_clip = AudioFileClip(str(voice_track))
            final = final.set_audio(voice_clip)

        if level_audio:
            logger.info("Audio leveling placeholder; integrate ffmpeg filters here")

        output_path = self.export_dir / "final_video.mp4"
        final.write_videofile(str(output_path))
        for clip in clips:
            clip.close()
        if voice_track:
            voice_clip.close()
        final.close()
        return output_path
