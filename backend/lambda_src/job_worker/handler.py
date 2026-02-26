from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

from job_worker.models import ClipTask, JobContext, JobMetadata
from job_worker.workflow import PipelineWorkflow

# Ensure INFO-level logs from the aivideomaker package reach CloudWatch.
# The Lambda root logger defaults to WARNING, which silently drops INFO calls.
logging.getLogger("aivideomaker").setLevel(logging.INFO)


def handler(event: Dict[str, Any], _context: Any) -> Dict[str, Any]:  # pragma: no cover
    action = event.get("action")
    workflow = PipelineWorkflow()

    if action == "GENERATE_PROMPTS":
        metadata_payload = event.get("job") or event
        metadata = JobMetadata.from_event(metadata_payload)
        dry_run = event.get("dryRun")
        context = workflow.generate_prompts(metadata, dry_run=dry_run)
        return context.to_payload()

    if action == "GENERATE_CLIP":
        job_context = JobContext.from_payload(event["jobContext"])
        clip_id = str(event["clipId"])
        result = workflow.render_clip(ClipTask(job_context=job_context, clip_id=clip_id))
        result["jobId"] = job_context.job_id
        return result

    if action == "INITIATE_RENDER":
        task = ClipTask.from_payload(event)
        result = workflow.initiate_clip_render(task)
        result.setdefault("jobId", task.job_context.job_id)
        return result

    if action == "POLL_RENDER":
        task = ClipTask.from_payload(event)
        render_job = event.get("renderJob") or {}
        result = workflow.poll_clip_render(task, render_job)
        result.setdefault("jobId", task.job_context.job_id)
        return result

    if action == "DOWNLOAD_RENDER":
        task = ClipTask.from_payload(event)
        render_job = event.get("renderJob") or {}
        result = workflow.complete_clip_render(task, render_job)
        result.setdefault("jobId", task.job_context.job_id)
        return result

    if action == "STITCH_FINAL":
        job_context = JobContext.from_payload(event["jobContext"])
        result = workflow.stitch_final(job_context)
        result["jobId"] = job_context.job_id
        return result

    if action == "GENERATE_CAPTIONS":
        job_context = JobContext.from_payload(event["jobContext"])
        result = workflow.generate_captions(job_context)
        result["jobId"] = job_context.job_id
        return result

    if action == "MARK_RUNNING":
        metadata_payload = event.get("job") or event
        metadata = JobMetadata.from_event(metadata_payload)
        result = workflow.mark_running(metadata, metadata_payload)
        return result

    if action == "REGENERATE_THUMBNAIL":
        job_context = JobContext.from_payload(event["jobContext"])
        result = workflow.regenerate_thumbnail(job_context)
        result["jobId"] = job_context.job_id
        return result

    if action == "MARK_FAILED":
        state_payload: Dict[str, Any] = {}
        if isinstance(event.get("state"), dict):
            state_payload = event["state"]
        else:
            state_payload = event

        job_context_payload = event.get("jobContext") or state_payload.get("jobContext")
        job_context: Optional[JobContext] = None
        if isinstance(job_context_payload, dict):
            try:
                job_context = JobContext.from_payload(job_context_payload)
            except (KeyError, TypeError, ValueError):
                job_context = None

        job_payload = event.get("job") or state_payload.get("job") or state_payload
        job_metadata: Optional[JobMetadata] = None
        if isinstance(job_payload, dict) and "jobId" in job_payload and "articleUrl" in job_payload:
            try:
                job_metadata = JobMetadata.from_event(job_payload)
            except (KeyError, TypeError, ValueError):
                job_metadata = None

        error_payload = event.get("error") or state_payload.get("error")

        workflow.mark_failed(job_context, metadata=job_metadata, error=error_payload)

        job_id: Optional[str] = None
        if job_context:
            job_id = job_context.job_id
        elif job_metadata:
            job_id = job_metadata.job_id
        elif isinstance(job_payload, dict) and "jobId" in job_payload:
            job_id = str(job_payload["jobId"])

        if not job_id:
            raise ValueError("Unable to determine job id for failure update")

        message = _format_failure_message(error_payload) if error_payload else "Job pipeline failed"
        raise RuntimeError(message)

    raise ValueError(f"Unknown action '{action}'")


def _format_failure_message(payload: Any) -> str:
    if isinstance(payload, dict):
        if isinstance(payload.get("Cause"), str) and payload["Cause"].strip():
            cause = payload["Cause"].strip()
            try:
                parsed = json.loads(cause)
                if isinstance(parsed, dict):
                    return parsed.get("errorMessage") or cause
            except json.JSONDecodeError:
                pass
            return cause
        for key in ("errorMessage", "Error", "Cause"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return json.dumps(payload)[:400]
    if payload is None:
        return "Job pipeline failed"
    return str(payload)
