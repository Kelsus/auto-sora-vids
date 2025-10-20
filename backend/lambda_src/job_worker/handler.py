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

    if action == "STITCH_FINAL":
        job_context = JobContext.from_payload(event["jobContext"])
        result = workflow.stitch_final(job_context)
        result["jobId"] = job_context.job_id
        return result

    if action == "MARK_FAILED":
        job_context_payload = event.get("jobContext")
        if job_context_payload:
            job_context = JobContext.from_payload(job_context_payload)
        else:
            metadata = JobMetadata.from_event(event.get("job") or event)
            settings = workflow._settings  # internal reuse
            job_context = JobContext(
                job_id=metadata.job_id,
                article_url=metadata.article_url,
                bundle_key=settings.bundle_key(metadata.job_id),
                output_prefix=settings.run_prefix(metadata.job_id),
                clip_ids=[],
                dry_run=settings.default_dry_run,
                social_media=metadata.social_media,
            )
        workflow.mark_failed(job_context, error=event.get("error"))
        return {"jobId": job_context.job_id, "status": "FAILED"}

    raise ValueError(f"Unknown action '{action}'")
