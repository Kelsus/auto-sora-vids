from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from aivideomaker.media_pipeline.elevenlabs_client import ElevenLabsClient, ElevenLabsResult

logger = logging.getLogger(__name__)


@dataclass
class NarrationAsset:
    transcript_path: Path
    audio_path: Optional[Path] = None
    alignment_path: Optional[Path] = None
    alignment_payload: Optional[dict] = None


class VoiceSessionManager:
    """Manages synthesized narration assets."""

    def __init__(
        self,
        base_dir: Path,
        *,
        eleven_client: ElevenLabsClient | None = None,
        default_voice_id: str | None = None,
    ) -> None:
        self._base_dir = Path(base_dir)
        self.eleven_client = eleven_client
        self.default_voice_id = default_voice_id

    @property
    def base_dir(self) -> Path:
        return self._base_dir

    @base_dir.setter
    def base_dir(self, value: Path) -> None:
        self._base_dir = Path(value)

    def prepare_voice(
        self,
        *,
        script_text: str,
        voice_id: str | None,
        dry_run: bool = True,
    ) -> NarrationAsset:
        voice_key = voice_id or self.default_voice_id or (
            getattr(self.eleven_client, "voice_id", None) if self.eleven_client else None
        )
        voice_dir = self.base_dir / (voice_key or "default")
        voice_dir.mkdir(parents=True, exist_ok=True)

        transcript_path = voice_dir / "transcript.txt"
        transcript_path.write_text(script_text, encoding="utf-8")

        if not script_text.strip():
            logger.warning("Empty script provided for narration; returning transcript only")
            return NarrationAsset(transcript_path=transcript_path)

        if dry_run:
            logger.info("Dry run: captured transcript for voice %s", voice_key or "default")
            return NarrationAsset(transcript_path=transcript_path, audio_path=None, alignment_path=None)

        if not self.eleven_client:
            raise RuntimeError("ElevenLabs client not configured; cannot synthesize narration")

        audio_path = voice_dir / f"narration.{self.eleven_client.audio_format}"
        alignment_path = voice_dir / "narration_alignment.json"

        result: ElevenLabsResult = self.eleven_client.synthesize(
            text=script_text,
            output_audio=audio_path,
            alignment_path=alignment_path,
            voice_id=voice_key,
        )

        if result.alignment_payload is not None:
            result.alignment_payload.pop("audio_base64", None)

        if result.alignment_path is None:
            alignment_path = None

        return NarrationAsset(
            transcript_path=transcript_path,
            audio_path=audio_path,
            alignment_path=alignment_path,
            alignment_payload=result.alignment_payload,
        )
