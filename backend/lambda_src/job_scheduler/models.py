from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional


@dataclass(frozen=True)
class ScheduledJob:
    job_id: str
    url: str
    social_media: Optional[str]
    scheduled_datetime: str

    @classmethod
    def from_item(cls, item: Mapping[str, Any]) -> "ScheduledJob":
        return cls(
            job_id=item["jobId"],
            url=item["url"],
            social_media=item.get("social_media"),
            scheduled_datetime=item.get("scheduled_datetime", ""),
        )
