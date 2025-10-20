from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Mapping

from aivideomaker.article_ingest.model import slugify


class ValidationError(ValueError):
    """Raised when the request payload cannot be processed."""


@dataclass(frozen=True)
class JobRequest:
    url: str
    social_media: str
    scheduled_datetime: datetime
    status: str = "PENDING"
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "JobRequest":
        missing = [field for field in ("url", "social_media", "scheduled_datetime") if not payload.get(field)]
        if missing:
            raise ValidationError(f"Missing required fields: {', '.join(missing)}")

        status = str(payload.get("status", "PENDING")).upper()
        if status not in {"PENDING", "QUEUED", "RUNNING", "COMPLETED", "FAILED"}:
            raise ValidationError(
                "status must be one of PENDING, QUEUED, RUNNING, COMPLETED, FAILED"
            )

        scheduled = cls._parse_datetime(str(payload["scheduled_datetime"]))

        return cls(
            url=str(payload["url"]),
            social_media=str(payload["social_media"]),
            scheduled_datetime=scheduled,
            status=status,
            metadata=payload.get("metadata", {}) or {},
        )

    @staticmethod
    def _parse_datetime(value: str) -> datetime:
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    @property
    def job_id(self) -> str:
        return slugify(self.url)
