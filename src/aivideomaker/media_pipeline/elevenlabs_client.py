from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

import requests

logger = logging.getLogger(__name__)


class ElevenLabsError(RuntimeError):
    """Raised when the ElevenLabs API reports an error."""


@dataclass
class ElevenLabsResult:
    audio_path: Path
    alignment_path: Optional[Path]
    alignment_payload: Optional[Dict[str, Any]]


class ElevenLabsClient:
    """Thin wrapper around the ElevenLabs Text-to-Speech API."""

    def __init__(
        self,
        api_key: str,
        default_voice_id: str | None = None,
        model_id: str = "eleven_turbo_v2",
        base_url: str = "https://api.elevenlabs.io",
        voice_settings: Optional[Dict[str, Any]] = None,
        enable_timestamps: bool = True,
        audio_format: str = "mp3",
        request_timeout: float = 60.0,
    ) -> None:
        if not api_key:
            raise ValueError("ElevenLabs API key is required")
        self.api_key = api_key
        self.voice_id = default_voice_id
        self.model_id = model_id
        self.base_url = base_url.rstrip("/")
        self.voice_settings = voice_settings or {"stability": 0.3, "similarity_boost": 0.75}
        self.enable_timestamps = enable_timestamps
        self.audio_format = audio_format
        self.request_timeout = request_timeout

    # ------------------------------------------------------------------
    def synthesize(
        self,
        text: str,
        output_audio: Path,
        alignment_path: Path | None = None,
        voice_id: str | None = None,
    ) -> ElevenLabsResult:
        """Render narration audio and optionally capture alignment data."""

        voice = voice_id or self.voice_id
        if not voice:
            raise ValueError("A voice_id must be provided to synthesize narration")

        output_audio.parent.mkdir(parents=True, exist_ok=True)
        alignment_payload: Optional[Dict[str, Any]] = None

        if self.enable_timestamps and alignment_path is not None:
            try:
                alignment_payload = self._synthesize_with_timestamps(
                    text=text,
                    voice_id=voice,
                    output_audio=output_audio,
                    alignment_path=alignment_path,
                )
            except ElevenLabsError as exc:
                logger.warning("Falling back to standard synthesis: %s", exc)

        if alignment_payload is None:
            self._synthesize_basic(text=text, voice_id=voice, output_audio=output_audio)
            if alignment_path is not None and alignment_path.exists():
                alignment_path.unlink()
            alignment_path = None

        return ElevenLabsResult(
            audio_path=output_audio,
            alignment_path=alignment_path,
            alignment_payload=alignment_payload,
        )

    # ------------------------------------------------------------------
    def _payload(self, text: str) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "text": text,
            "model_id": self.model_id,
            "voice_settings": self.voice_settings,
        }
        return payload

    def _headers(self, accept: str) -> Dict[str, str]:
        return {
            "xi-api-key": self.api_key,
            "accept": accept,
            "content-type": "application/json",
        }

    def _synthesize_with_timestamps(
        self,
        *,
        text: str,
        voice_id: str,
        output_audio: Path,
        alignment_path: Path,
    ) -> Dict[str, Any]:
        url = f"{self.base_url}/v1/text-to-speech/{voice_id}/with-timestamps"
        response = requests.post(
            url,
            headers=self._headers("application/json"),
            json=self._payload(text),
            timeout=self.request_timeout,
        )
        if response.status_code >= 400:
            raise ElevenLabsError(self._format_error(response))

        data = response.json()
        audio_b64 = data.get("audio_base64") or data.get("audio")
        if not audio_b64:
            raise ElevenLabsError("ElevenLabs response missing audio payload")
        audio_bytes = base64.b64decode(audio_b64)
        output_audio.write_bytes(audio_bytes)

        alignment_path.parent.mkdir(parents=True, exist_ok=True)
        alignment_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return data

    def _synthesize_basic(
        self,
        *,
        text: str,
        voice_id: str,
        output_audio: Path,
    ) -> None:
        url = f"{self.base_url}/v1/text-to-speech/{voice_id}"
        response = requests.post(
            url,
            headers=self._headers(f"audio/{self.audio_format}"),
            json=self._payload(text),
            timeout=self.request_timeout,
        )
        if response.status_code >= 400:
            raise ElevenLabsError(self._format_error(response))
        output_audio.write_bytes(response.content)

    @staticmethod
    def _format_error(response: requests.Response) -> str:
        try:
            payload = response.json()
            message = payload.get("detail") or payload
        except ValueError:
            message = response.text
        return f"{response.status_code} {message}"
