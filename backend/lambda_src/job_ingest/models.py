from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Mapping

from aivideomaker.article_ingest.model import slug_from_url
from common.time_utils import ensure_utc, utc_now


class ValidationError(ValueError):
    """Raised when the request payload cannot be processed."""


MAX_OVERRIDE_LENGTH = 200_000


@dataclass(frozen=True)
class JobRequest:
    url: str
    scheduled_datetime: datetime
    job_type: str = "SCHEDULED"
    status: str = "PENDING"
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "JobRequest":
        missing = [field for field in ("url",) if not payload.get(field)]
        if missing:
            raise ValidationError(f"Missing required fields: {', '.join(missing)}")

        job_type_raw = payload.get("job_type")
        job_type = str(job_type_raw or "SCHEDULED").upper()
        if job_type not in {"SCHEDULED", "IMMEDIATE"}:
            raise ValidationError("job_type must be one of SCHEDULED, IMMEDIATE")

        status = str(payload.get("status", "PENDING")).upper()
        allowed_statuses = {
            "PENDING",
            "REVIEW",
            "REVISION_REQUESTED",
            "REJECTED",
            "QUEUED",
            "RUNNING",
            "COMPLETED",
            "FAILED",
            "CANCELED",
        }
        if status not in allowed_statuses:
            allowed_csv = ", ".join(sorted(allowed_statuses))
            raise ValidationError(
                f"status must be one of {allowed_csv}"
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
            pipeline_config_dict = dict(pipeline_config)
            if "drive_folder" in pipeline_config_dict:
                drive_folder = pipeline_config_dict["drive_folder"]
                if not isinstance(drive_folder, str) or not drive_folder.strip():
                    raise ValidationError("pipeline_config.drive_folder must be a non-empty string")
                pipeline_config_dict["drive_folder"] = drive_folder.strip()
            metadata["pipeline_config"] = pipeline_config_dict

        input_image_keys = payload.get("input_image_keys") or payload.get("inputImageKeys")
        if input_image_keys is not None:
            if not isinstance(input_image_keys, list):
                raise ValidationError("input_image_keys must be a list")
            if len(input_image_keys) > 3:
                raise ValidationError("At most 3 images may be provided")
            for i, key in enumerate(input_image_keys):
                if not isinstance(key, str):
                    raise ValidationError(f"input_image_keys[{i}] must be a string")
                if not key.startswith("jobs/"):
                    raise ValidationError(f"input_image_keys[{i}] has invalid format")
            if "pipeline_config" not in metadata:
                metadata["pipeline_config"] = {}
            metadata["pipeline_config"]["input_image_keys"] = input_image_keys

        article_override = cls._parse_article_override(
            payload.get("article_override") or payload.get("articleOverride")
        )
        if article_override:
            metadata["article_override"] = article_override

        return cls(
            url=str(payload["url"]),
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
        return slug_from_url(self.url)

    @staticmethod
    def _parse_article_override(raw: Any) -> Dict[str, Any] | None:
        if raw is None:
            return None
        if not isinstance(raw, Mapping):
            raise ValidationError("article_override must be an object")

        text = raw.get("text")
        if not isinstance(text, str) or not text.strip():
            raise ValidationError("article_override.text must be a non-empty string")
        normalized_text = text.strip()
        if len(normalized_text) > MAX_OVERRIDE_LENGTH:
            raise ValidationError(
                f"article_override.text must be {MAX_OVERRIDE_LENGTH} characters or fewer"
            )

        override: Dict[str, Any] = {"text": normalized_text}

        for field in ("title", "source", "byline"):
            value = raw.get(field)
            if isinstance(value, str) and value.strip():
                override[field] = value.strip()

        published_raw = raw.get("published_at") or raw.get("publishedAt")
        if isinstance(published_raw, str) and published_raw.strip():
            try:
                published_dt = JobRequest._parse_datetime(published_raw.strip())
            except ValueError as exc:
                raise ValidationError("article_override.published_at must be ISO-8601") from exc
            override["published_at"] = published_dt.isoformat()

        return override
