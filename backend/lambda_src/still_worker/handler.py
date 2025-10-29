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
    
    # Check render_mode from prompt FIRST (overrides beat visual type)
    prompt = next((p for p in bundle.prompts.media_prompts if p.chunk_id == task.clip_id), None)
    if prompt and getattr(prompt, "render_mode", None) == "sora_clip":
        logger.debug(
            "Skipping still generation for %s; render_mode=sora_clip (forced Sora)",
            task.clip_id,
        )
        return {"clipId": task.clip_id, "skipped": True, "reason": "forced_sora"}
    
    visual_type = _resolve_visual_type(bundle, task.clip_id)
    if visual_type not in {"still_motion", "still"}:
        logger.debug(
            "Skipping still generation for %s; visual_type=%s",
            task.clip_id,
            visual_type,
        )
        return {"clipId": task.clip_id, "skipped": True, "reason": "not_still"}

    result = workflow.render_clip(task)
    result["mode"] = "still"
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
