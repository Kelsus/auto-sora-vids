from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional


@dataclass(frozen=True)
class ScheduledJob:
    job_id: str
    url: str
    social_media: Optional[str]
    scheduled_datetime: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    pipeline_config: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_item(cls, item: Mapping[str, Any]) -> "ScheduledJob":
        metadata_raw = item.get("metadata") or {}
        metadata: Dict[str, Any]
        if isinstance(metadata_raw, Mapping):
            metadata = dict(metadata_raw)
        else:
            metadata = {}
        pipeline_config_raw = metadata.get("pipeline_config")
        pipeline_config: Dict[str, Any]
        if isinstance(pipeline_config_raw, Mapping):
            pipeline_config = dict(pipeline_config_raw)
        else:
            pipeline_config = {}
        return cls(
            job_id=item["jobId"],
            url=item["url"],
            social_media=item.get("social_media"),
            scheduled_datetime=item.get("scheduled_datetime", ""),
            metadata=metadata,
            pipeline_config=pipeline_config,
        )

    def to_message(self) -> Dict[str, Any]:
        """Serialize the job for transport across the dispatch queue."""
        payload: Dict[str, Any] = {
            "jobId": self.job_id,
            "url": self.url,
            "scheduled_datetime": self.scheduled_datetime,
            "metadata": self.metadata,
        }
        if self.social_media is not None:
            payload["social_media"] = self.social_media
        return payload
