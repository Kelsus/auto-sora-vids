from __future__ import annotations

import logging
from typing import Any, Dict

from job_worker.models import ClipTask
from job_worker.workflow import PipelineWorkflow

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def handler(event: Dict[str, Any], _context: Any) -> Dict[str, Any]:  # pragma: no cover - thin wrapper
    workflow = PipelineWorkflow()
    task = ClipTask.from_payload(event)

    bundle = workflow._bundle_store.load(task.job_context.bundle_key)
    visual_type = _resolve_visual_type(bundle, task.clip_id)
    if visual_type != "chart":
        logger.debug(
            "Skipping chart generation for %s; visual_type=%s",
            task.clip_id,
            visual_type,
        )
        return {"clipId": task.clip_id, "skipped": True, "reason": "not_chart"}

    result = workflow.render_clip(task)
    result["mode"] = "chart"
    return result


def _resolve_visual_type(bundle, clip_id: str) -> str:
    chunk_ref = next(
        (c for c in bundle.chunks.chunks if getattr(c, "id", c.beat_id) == clip_id),
        None,
    )
    beat_id = chunk_ref.beat_id if chunk_ref else clip_id
    beat = next((b for b in bundle.script.beats if b.id == beat_id), None)
    if beat and beat.visual and beat.visual.type:
        return beat.visual.type.lower()
    return "cinematic_broll"
