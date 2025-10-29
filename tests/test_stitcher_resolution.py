from __future__ import annotations

from pathlib import Path

import array
import wave

import pytest
from moviepy.editor import ColorClip, VideoFileClip

from aivideomaker.stitcher.assembler import Stitcher


def _write_color_clip(
    path: Path,
    size: tuple[int, int],
    color: tuple[int, int, int],
    *,
    duration: float = 0.5,
) -> None:
    clip = ColorClip(size=size, color=color, duration=duration)
    clip.write_videofile(
        str(path),
        fps=24,
        codec="libx264",
        audio=False,
        logger=None,
    )
    clip.close()


def _write_silence(path: Path, duration: float) -> None:
    sample_rate = 44100
    total_samples = int(duration * sample_rate)
    samples = array.array("h", [0] * total_samples)
    with wave.open(str(path), "w") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(samples.tobytes())


def test_stitcher_preserves_original_dimensions(tmp_path) -> None:
    clip_a = tmp_path / "clip_a.mp4"
    clip_b = tmp_path / "clip_b.mp4"
    _write_color_clip(clip_a, (128, 256), (255, 0, 0))
    _write_color_clip(clip_b, (128, 256), (0, 255, 0))

    stitcher = Stitcher(export_dir=tmp_path / "exports")
    output_path = stitcher.stitch([clip_a, clip_b])

    final = VideoFileClip(str(output_path))
    try:
        assert tuple(final.size) == (128, 256)
        first_frame = final.get_frame(0.1)
        # Ensure the top-left pixel is not letterboxed (near-black).
        assert first_frame[0, 0, 0] > 50
    finally:
        final.close()


def test_stitcher_normalizes_mismatched_dimensions(tmp_path) -> None:
    dominant_size = (128, 256)
    off_size = (160, 320)  # Still 1:2 aspect but slightly larger

    clip_primary = tmp_path / "clip_primary.mp4"
    clip_off = tmp_path / "clip_off.mp4"
    clip_secondary = tmp_path / "clip_secondary.mp4"

    _write_color_clip(clip_primary, dominant_size, (255, 0, 0))
    _write_color_clip(clip_off, off_size, (0, 0, 255))
    _write_color_clip(clip_secondary, dominant_size, (0, 255, 0))

    stitcher = Stitcher(export_dir=tmp_path / "exports2")
    output_path = stitcher.stitch([clip_primary, clip_off, clip_secondary])

    final = VideoFileClip(str(output_path))
    try:
        assert tuple(final.size) == dominant_size
        frame_primary = final.get_frame(0.1)
        frame_secondary = final.get_frame(0.8)
        assert frame_primary[0, 0].sum() > 50  # not letterboxed
        assert frame_secondary[0, 0].sum() > 50
    finally:
        final.close()


def test_stitcher_extends_video_for_longer_voice(tmp_path) -> None:
    clip_a = tmp_path / "clip_a.mp4"
    clip_b = tmp_path / "clip_b.mp4"
    voice_path = tmp_path / "voice.wav"

    _write_color_clip(clip_a, (96, 96), (255, 0, 0), duration=1.0)
    _write_color_clip(clip_b, (96, 96), (0, 255, 0), duration=1.0)
    _write_silence(voice_path, duration=4.0)

    stitcher = Stitcher(export_dir=tmp_path / "exports3")
    output_path = stitcher.stitch([clip_a, clip_b], voice_track=voice_path)

    final = VideoFileClip(str(output_path))
    try:
        assert final.duration == pytest.approx(4.0, abs=0.2)
        assert final.audio is not None
        assert final.audio.duration == pytest.approx(4.0, abs=0.2)
    finally:
        final.close()
