from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from job_list.http import cors_preflight_response, ok, server_error

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

VOICE_API_URL = os.environ.get("ELEVEN_LABS_VOICE_API_URL", "https://api.elevenlabs.io/v1/voices")
def _parse_filter(var_name: str) -> set[str]:
    raw = os.environ.get(var_name, "")
    values = {entry.strip().lower() for entry in raw.split(",") if entry.strip()}
    return values


TARGET_LANGUAGES: set[str] = _parse_filter("VOICE_LIST_LANGUAGES")
TARGET_QUALITIES: set[str] = _parse_filter("VOICE_LIST_QUALITIES") or {"highest"}
TARGET_TYPES: set[str] = _parse_filter("VOICE_LIST_TYPES")
MAX_RESULTS = 60

DEFAULT_FALLBACK_VOICES: List[Dict[str, Any]] = [
    {
        "voiceId": "gfRt6Z3Z8aTbpLfexQ7N",
        "name": "Internal Narrator",
        "labels": {"quality": "highest", "use_case": "narrative & story"},
    },
    {
        "voiceId": "21m00Tcm4TlvDq8ikWAM",
        "name": "Rachel",
        "labels": {"quality": "highest", "use_case": "conversational"},
    },
    {
        "voiceId": "AZnzlk1XvdvUeBnXmlld",
        "name": "Domi",
        "labels": {"quality": "highest", "use_case": "narrative & story"},
    },
    {
        "voiceId": "ErXwobaYiN019PkySvjV",
        "name": "Antoni",
        "labels": {"quality": "highest", "use_case": "social media"},
    },
]


class VoiceListPermissionError(RuntimeError):
    """Raised when the upstream voice API rejects our request."""


class VoiceListApplication:
    """Fetches ElevenLabs voices and exposes a filtered list to the dashboard."""

    def __init__(self, *, ssm_client: Any | None = None) -> None:
        self._cached_key: Optional[str] = None
        self._ssm = ssm_client

    def handle_event(self, event: Dict[str, Any]) -> Dict[str, Any]:
        logger.info("Voice list event received")
        if event.get("httpMethod") == "OPTIONS":  # CORS preflight
            return cors_preflight_response()

        try:
            voices = self._load_filtered_voices()
            if not voices:
                voices = self._load_fallback_voices()
        except VoiceListPermissionError:
            logger.warning("Falling back to static voice list; ElevenLabs rejected request")
            voices = self._load_fallback_voices()
        except Exception:  # pylint: disable=broad-except
            logger.exception("Failed to load ElevenLabs voices")
            fallback = self._load_fallback_voices()
            if fallback:
                logger.warning("Serving fallback voice list after unexpected failure")
                return ok({"voices": fallback})
            return server_error("Failed to load available voices")

        return ok({"voices": voices})

    def _load_filtered_voices(self) -> List[Dict[str, Any]]:
        try:
            raw_voices = self._fetch_voices()
        except HTTPError as exc:
            if getattr(exc, "code", None) in {401, 403}:
                raise VoiceListPermissionError("ElevenLabs API rejected credentials") from exc
            raise
        logger.info("Fetched %s voices from ElevenLabs", len(raw_voices))
        filtered: List[Dict[str, Any]] = []
        for entry in raw_voices:
            if not isinstance(entry, dict):
                continue
            if not self._matches_filters(entry):
                continue
            mapped = self._map_voice(entry)
            if mapped:
                filtered.append(mapped)
            if len(filtered) >= MAX_RESULTS:
                break
        if filtered:
            logger.info("Returning %s filtered voices", len(filtered))
            return filtered

        logger.warning("Filtered voice list is empty; falling back to static defaults")
        return []

    def _fetch_voices(self) -> List[Dict[str, Any]]:
        api_key = self._resolve_api_key()
        request = Request(VOICE_API_URL, headers={"xi-api-key": api_key})
        try:
            with urlopen(request, timeout=10) as response:  # nosec B310
                payload = response.read().decode("utf-8")
        except HTTPError as exc:
            logger.error("ElevenLabs voices request failed: %s", exc)
            raise
        except URLError as exc:
            logger.error("Unable to reach ElevenLabs API: %s", exc)
            raise

        try:
            data = json.loads(payload)
        except json.JSONDecodeError as exc:
            logger.error("Failed to parse ElevenLabs response: %s", exc)
            raise

        voices = data.get("voices")
        if isinstance(voices, list):
            return [entry for entry in voices if isinstance(entry, dict)]
        logger.warning("Unexpected ElevenLabs response shape: %s", type(voices))
        return []

    def _resolve_api_key(self) -> str:
        if self._cached_key:
            return self._cached_key

        direct = os.environ.get("ELEVEN_LABS_API_KEY")
        if direct:
            self._cached_key = direct.strip()
            return self._cached_key

        parameter_name = os.environ.get("ELEVEN_LABS_API_KEY_PARAMETER")
        if not parameter_name:
            raise RuntimeError("ELEVEN_LABS_API_KEY or ELEVEN_LABS_API_KEY_PARAMETER must be configured")

        if self._ssm is None:
            self._ssm = boto3.client("ssm")

        try:
            response = self._ssm.get_parameter(Name=parameter_name, WithDecryption=True)
        except (BotoCoreError, ClientError) as exc:
            logger.error("Failed to read ElevenLabs key from SSM: %s", exc)
            raise RuntimeError("Unable to load ElevenLabs API key") from exc

        value = response.get("Parameter", {}).get("Value")
        if not value:
            raise RuntimeError("ElevenLabs API key parameter is empty")

        self._cached_key = value.strip()
        return self._cached_key

    def _matches_filters(self, voice: Dict[str, Any]) -> bool:
        labels = voice.get("labels") if isinstance(voice.get("labels"), dict) else {}
        quality = str(labels.get("quality") or "").lower()
        if TARGET_QUALITIES:
            if quality:
                if quality not in TARGET_QUALITIES:
                    return False
            else:
                return False

        language = str(labels.get("language") or voice.get("language") or "").lower()
        if TARGET_LANGUAGES and language:
            if not any(lang in language for lang in TARGET_LANGUAGES):
                return False

        type_candidates = self._collect_type_candidates(voice, labels)
        if TARGET_TYPES and type_candidates:
            if not any(any(target in candidate for target in TARGET_TYPES) for candidate in type_candidates):
                return False
        elif TARGET_TYPES:
            return False

        return True

    @staticmethod
    def _collect_type_candidates(voice: Dict[str, Any], labels: Dict[str, Any]) -> List[str]:
        fields = [
            "use_case",
            "category",
            "style",
            "description",
            "accent",
        ]
        candidates: List[str] = []
        for field in fields:
            value = labels.get(field)
            if isinstance(value, str) and value.strip():
                candidates.append(value.strip().lower())
        for extra_field in ("category", "description"):
            extra_value = voice.get(extra_field)
            if isinstance(extra_value, str) and extra_value.strip():
                candidates.append(extra_value.strip().lower())
        return candidates

    @staticmethod
    def _map_voice(entry: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        voice_id = entry.get("voice_id") or entry.get("voiceId") or entry.get("id")
        name = entry.get("name")
        if not isinstance(voice_id, str) or not isinstance(name, str):
            return None
        mapped: Dict[str, Any] = {
            "voiceId": voice_id,
            "name": name,
        }
        labels = entry.get("labels")
        if isinstance(labels, dict):
            mapped["labels"] = labels
        if isinstance(entry.get("category"), str):
            mapped["category"] = entry["category"]
        if isinstance(entry.get("preview_url"), str):
            mapped["previewUrl"] = entry["preview_url"]
        return mapped

    def _load_fallback_voices(self) -> List[Dict[str, Any]]:
        override = os.environ.get("VOICE_LIST_FALLBACK_JSON")
        if override:
            try:
                candidate = json.loads(override)
                voices = self._normalize_fallback(candidate)
                if voices:
                    return voices
            except json.JSONDecodeError:
                logger.warning("Invalid VOICE_LIST_FALLBACK_JSON provided; falling back to defaults")
        return list(DEFAULT_FALLBACK_VOICES)

    def _normalize_fallback(self, candidate: Any) -> List[Dict[str, Any]]:
        if not isinstance(candidate, list):
            return []
        normalized: List[Dict[str, Any]] = []
        for entry in candidate:
            if isinstance(entry, dict):
                mapped = self._map_voice(entry)
                if mapped:
                    normalized.append(mapped)
        return normalized


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:  # pragma: no cover - AWS entry
    return VoiceListApplication().handle_event(event)
