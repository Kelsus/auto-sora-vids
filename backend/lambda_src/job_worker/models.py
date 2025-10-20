from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class JobMetadata:
    job_id: str
    article_url: str
    social_media: Optional[str] = None
    scheduled_datetime: Optional[str] = None

    @classmethod
    def from_event(cls, payload: Dict[str, Any]) -> "JobMetadata":
        return cls(
            job_id=str(payload["jobId"]),
            article_url=str(payload["articleUrl"]),
            social_media=payload.get("socialMedia"),
            scheduled_datetime=payload.get("scheduledDatetime"),
        )


@dataclass
class JobContext:
    job_id: str
    article_url: str
    bundle_key: str
    output_prefix: str
    clip_ids: List[str]
    dry_run: bool
    social_media: Optional[str] = None

    def to_payload(self) -> Dict[str, Any]:
        return {
            "jobId": self.job_id,
            "articleUrl": self.article_url,
            "bundleKey": self.bundle_key,
            "outputPrefix": self.output_prefix,
            "clipIds": self.clip_ids,
            "dryRun": self.dry_run,
            "socialMedia": self.social_media,
        }

    @classmethod
    def from_payload(cls, payload: Dict[str, Any]) -> "JobContext":
        return cls(
            job_id=str(payload["jobId"]),
            article_url=str(payload["articleUrl"]),
            bundle_key=str(payload["bundleKey"]),
            output_prefix=str(payload["outputPrefix"]),
            clip_ids=list(payload.get("clipIds", [])),
            dry_run=bool(payload.get("dryRun", False)),
            social_media=payload.get("socialMedia"),
        )


@dataclass(frozen=True)
class ClipTask:
    job_context: JobContext
    clip_id: str

    @classmethod
    def from_payload(cls, payload: Dict[str, Any]) -> "ClipTask":
        context = JobContext.from_payload(payload["jobContext"])
        return cls(job_context=context, clip_id=str(payload["clipId"]))


@dataclass
class JobStatusUpdate:
    status: str
    attributes: Dict[str, Any]