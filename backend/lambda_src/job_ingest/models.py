from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Mapping

from aivideomaker.article_ingest.model import slugify
from common.time_utils import ensure_utc, utc_now


class ValidationError(ValueError):
    """Raised when the request payload cannot be processed."""


@dataclass(frozen=True)
class JobRequest:
    url: str
    social_media: str
    scheduled_datetime: datetime
    job_type: str = "SCHEDULED"
    status: str = "PENDING"
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "JobRequest":
        missing = [field for field in ("url", "social_media") if not payload.get(field)]
        if missing:
            raise ValidationError(f"Missing required fields: {', '.join(missing)}")

        job_type_raw = payload.get("job_type")
        job_type = str(job_type_raw or "SCHEDULED").upper()
        if job_type not in {"SCHEDULED", "IMMEDIATE"}:
            raise ValidationError("job_type must be one of SCHEDULED, IMMEDIATE")

        status = str(payload.get("status", "PENDING")).upper()
        if status not in {"PENDING", "QUEUED", "RUNNING", "COMPLETED", "FAILED"}:
            raise ValidationError(
                "status must be one of PENDING, QUEUED, RUNNING, COMPLETED, FAILED"
            )

        scheduled_input = payload.get("scheduled_datetime")
        if job_type == "SCHEDULED" and not scheduled_input:
            raise ValidationError("scheduled_datetime is required for SCHEDULED jobs")

        if scheduled_input:
            scheduled = cls._parse_datetime(str(scheduled_input))
        else:
            scheduled = ensure_utc(utc_now())

        metadata_raw = payload.get("metadata") or {}
        if not isinstance(metadata_raw, Mapping):
            raise ValidationError("metadata must be an object")
        metadata = dict(metadata_raw)

        pipeline_config = payload.get("pipeline_config")
        if pipeline_config is None:
            pipeline_config = payload.get("pipelineConfig")
        if pipeline_config:
            if not isinstance(pipeline_config, Mapping):
                raise ValidationError("pipeline_config must be an object")
            metadata["pipeline_config"] = dict(pipeline_config)

        return cls(
            url=str(payload["url"]),
            social_media=str(payload["social_media"]),
            scheduled_datetime=scheduled,
            job_type=job_type,
            status=status,
            metadata=metadata,
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
