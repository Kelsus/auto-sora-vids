from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Set

from common.time_utils import ensure_utc


ALLOWED_FIELDS = {"status", "job_type", "pipeline_config", "scheduled_datetime"}


@dataclass(frozen=True)
class JobUpdatePayload:
    status: str | None
    job_type: str | None
    pipeline_config: Dict[str, Any] | None
    scheduled_datetime: datetime | None
    _provided: Set[str]

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "JobUpdatePayload":
        if not isinstance(data, dict):
            raise ValueError("Body must be a JSON object")

        provided = {key for key in data.keys() if key in ALLOWED_FIELDS}
        if not provided:
            unexpected = set(data.keys()) - ALLOWED_FIELDS
            if unexpected:
                raise ValueError(f"Unsupported field(s): {', '.join(sorted(unexpected))}")
            raise ValueError("Request must include at least one updatable field")

        unexpected = provided.symmetric_difference(set(data.keys()))
        extra_keys = set(data.keys()) - ALLOWED_FIELDS
        if extra_keys:
            raise ValueError(f"Unsupported field(s): {', '.join(sorted(extra_keys))}")

        status = cls._parse_optional_string(data, "status") if "status" in provided else None
        job_type = cls._parse_optional_string(data, "job_type") if "job_type" in provided else None
        pipeline_config = cls._parse_pipeline_config(data, provided)
        scheduled_datetime = cls._parse_scheduled_datetime(data) if "scheduled_datetime" in provided else None

        return cls(
            status=status,
            job_type=job_type,
            pipeline_config=pipeline_config,
            scheduled_datetime=scheduled_datetime,
            _provided=provided,
        )

    @staticmethod
    def _parse_optional_string(data: Dict[str, Any], field: str) -> str:
        value = data.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field} must be a non-empty string")
        return value.strip()

    @staticmethod
    def _parse_pipeline_config(data: Dict[str, Any], provided: Set[str]) -> Dict[str, Any] | None:
        if "pipeline_config" not in provided:
            return None
        value = data.get("pipeline_config")
        if value is None:
            return None
        if not isinstance(value, dict):
            raise ValueError("pipeline_config must be an object when provided")
        return dict(value)

    @staticmethod
    def _parse_scheduled_datetime(data: Dict[str, Any]) -> datetime:
        raw = data.get("scheduled_datetime")
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError("scheduled_datetime must be an ISO 8601 string")
        normalized = raw.strip()
        if normalized.endswith("Z"):
            normalized = normalized[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError as exc:  # pragma: no cover - defensive
            raise ValueError("scheduled_datetime must be an ISO 8601 string") from exc
        return ensure_utc(parsed)

    def is_provided(self, field_name: str) -> bool:
        return field_name in self._provided
