from __future__ import annotations

import base64
import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

import requests

logger = logging.getLogger(__name__)


class ElevenLabsMusicError(RuntimeError):
    """Raised when the ElevenLabs music API reports a failure."""


class ElevenLabsMusicClient:
    """Wrapper around ElevenLabs music generation endpoint."""

    def __init__(
        self,
        api_key: str,
        output_dir: Path,
        *,
        base_url: str = "https://api.elevenlabs.io",
        model_id: str = "music_v1",
        force_instrumental: bool = True,
        output_format: str = "mp3_44100_128",
        request_timeout: float = 120.0,
    ) -> None:
        if not api_key:
            raise ValueError("ElevenLabs API key is required for music generation")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model_id = model_id
        self.force_instrumental = force_instrumental
        self.output_format = output_format
        self.request_timeout = request_timeout
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def compose(
        self,
        *,
        prompt: str,
        duration_sec: float,
        title: str | None = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Path:
        payload: Dict[str, Any] = {
            "prompt": prompt,
            "music_length_ms": int(max(duration_sec, 3.0) * 1000),
            "model_id": self.model_id,
            "output_format": self.output_format,
        }
        if self.force_instrumental:
            payload["force_instrumental"] = True
        if title:
            payload.setdefault("song_metadata", {})["title"] = title
        if metadata:
            payload.update(metadata)

        response = requests.post(
            f"{self.base_url}/v1/music/detailed",
            headers=self._headers(),
            json=payload,
            timeout=self.request_timeout,
        )
        if response.status_code >= 400:
            raise ElevenLabsMusicError(self._format_error(response))

        target = self._target_path()
        content_type = response.headers.get("Content-Type", "") or ""
        content_type_lower = content_type.lower()

        if "multipart/mixed" in content_type_lower:
            self._write_multipart_audio(response, target, content_type)
            return target

        if "application/json" in content_type_lower or not content_type:
            data = response.json()
            audio_field = data.get("audio") or data.get("audio_base64")
            if not audio_field:
                raise ElevenLabsMusicError("ElevenLabs response missing audio payload")
            audio_bytes = self._decode_audio(audio_field)
            target.write_bytes(audio_bytes)
            return target

        if "audio" in content_type_lower or "octet-stream" in content_type_lower:
            target.write_bytes(response.content)
            return target

        raise ElevenLabsMusicError(f"Unexpected ElevenLabs music response ({content_type})")

    # ------------------------------------------------------------------
    def _headers(self) -> Dict[str, str]:
        return {
            "xi-api-key": self.api_key,
            "Content-Type": "application/json",
            "Accept": "*/*",
        }

    def _decode_audio(self, value: Any) -> bytes:
        if isinstance(value, (bytes, bytearray)):
            return bytes(value)
        if isinstance(value, str):
            try:
                return base64.b64decode(value)
            except (ValueError, TypeError) as exc:  # pragma: no cover - unexpected format
                raise ElevenLabsMusicError("Failed to decode base64 audio payload") from exc
        raise ElevenLabsMusicError("Unsupported audio payload type")

    def _target_path(self) -> Path:
        return self.output_dir / "music_track.mp3"

    @staticmethod
    def _format_error(response: requests.Response) -> str:
        try:
            payload = response.json()
            message = payload.get("detail") or payload
        except ValueError:
            message = response.text
        return f"{response.status_code} {message}"

    def _write_multipart_audio(self, response: requests.Response, target: Path, content_type_header: str) -> None:
        content_type = content_type_header or ""
        try:
            # Boundary may appear as boundary=----... possibly with surrounding quotes
            boundary = content_type.split("boundary=")[1]
            boundary = boundary.strip().strip('"')
        except IndexError as exc:
            raise ElevenLabsMusicError("Multipart response missing boundary") from exc
        boundary_bytes = ("--" + boundary).encode()
        parts = response.content.split(boundary_bytes)
        audio_written = False
        for part in parts:
            if not part.strip():
                continue
            header_body = part.split(b"\r\n\r\n", 1)
            if len(header_body) != 2:
                continue
            headers_raw, body = header_body
            if b"Content-Type: audio" in headers_raw or b"Content-Type: application/octet-stream" in headers_raw:
                audio_end = body.rfind(b"\r\n")
                audio = body[:audio_end] if audio_end != -1 else body
                target.write_bytes(audio)
                audio_written = True
                break
        if not audio_written:
            raise ElevenLabsMusicError("Multipart response missing audio part")
