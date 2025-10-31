from __future__ import annotations

from typing import Any, Dict

from job_worker.models import ClipTask, JobContext, JobMetadata
from job_worker.workflow import PipelineWorkflow


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

    if action == "MARK_FAILED":
        job_context = JobContext.from_payload(event["jobContext"])
        workflow.mark_failed(job_context, error=event.get("error"))
        return {"jobId": job_context.job_id, "status": "FAILED"}

    raise ValueError(f"Unknown action '{action}'")
