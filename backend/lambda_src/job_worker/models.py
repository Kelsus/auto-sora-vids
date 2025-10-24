from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional


@dataclass(frozen=True)
class JobMetadata:
    job_id: str
    article_url: str
    scheduled_datetime: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    pipeline_config: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_event(cls, payload: Dict[str, Any]) -> "JobMetadata":
        metadata_raw = payload.get("metadata") or {}
        metadata: Dict[str, Any]
        if isinstance(metadata_raw, dict):
            metadata = dict(metadata_raw)
        elif isinstance(metadata_raw, Mapping):
            metadata = dict(metadata_raw)
        else:
            metadata = {}
        pipeline_config_raw = payload.get("pipelineConfig") or metadata.get("pipeline_config")
        pipeline_config: Dict[str, Any]
        if isinstance(pipeline_config_raw, Mapping):
            pipeline_config = dict(pipeline_config_raw)
        else:
            pipeline_config = {}
        return cls(
            job_id=str(payload["jobId"]),
            article_url=str(payload["articleUrl"]),
            scheduled_datetime=payload.get("scheduledDatetime"),
            metadata=metadata,
            pipeline_config=pipeline_config,
        )


@dataclass
class JobContext:
    job_id: str
    article_url: str
    bundle_key: str
    output_prefix: str
    clip_ids: List[str]
    dry_run: bool
    pipeline_config: Dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> Dict[str, Any]:
        return {
            "jobId": self.job_id,
            "articleUrl": self.article_url,
            "bundleKey": self.bundle_key,
            "outputPrefix": self.output_prefix,
            "clipIds": self.clip_ids,
            "dryRun": self.dry_run,
            "pipelineConfig": self.pipeline_config,
        }

    @classmethod
    def from_payload(cls, payload: Dict[str, Any]) -> "JobContext":
        pipeline_raw = payload.get("pipelineConfig") or {}
        pipeline_config: Dict[str, Any]
        if isinstance(pipeline_raw, Mapping):
            pipeline_config = dict(pipeline_raw)
        else:
            pipeline_config = {}
        return cls(
            job_id=str(payload["jobId"]),
            article_url=str(payload["articleUrl"]),
            bundle_key=str(payload["bundleKey"]),
            output_prefix=str(payload["outputPrefix"]),
            clip_ids=list(payload.get("clipIds", [])),
            dry_run=bool(payload.get("dryRun", False)),
            pipeline_config=pipeline_config,
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
