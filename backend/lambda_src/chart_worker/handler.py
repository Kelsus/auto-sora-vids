from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from job_worker.models import ClipTask
from job_worker.workflow import PipelineWorkflow

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:  # pragma: no cover - thin wrapper
    workflow = PipelineWorkflow()
    task = ClipTask.from_payload(event)

    logger.info(
        "Chart worker invoked clip=%s force_preprocess=%s request_id=%s",
        task.clip_id,
        task.force_preprocess,
        getattr(context, "aws_request_id", "unknown"),
    )

    bundle = workflow._bundle_store.load(task.job_context.bundle_key)
    chunk = _find_chunk(bundle, task.clip_id)
    beat_id = getattr(chunk, "beat_id", task.clip_id)

    # Check render_mode from prompt FIRST (overrides beat visual type)
    prompt = next((p for p in bundle.prompts.media_prompts if p.chunk_id == task.clip_id), None)
    if prompt and getattr(prompt, "render_mode", None) == "sora_clip":
        logger.info("Skipping chart generation clip=%s reason=forced_sora", task.clip_id)
        return {"clipId": task.clip_id, "skipped": True, "reason": "forced_sora"}

    visual_type = _resolve_visual_type(bundle, task.clip_id)
    if visual_type != "chart":
        logger.info("Skipping non-chart clip=%s visual_type=%s", task.clip_id, visual_type)
        return {"clipId": task.clip_id, "skipped": True, "reason": "not_chart"}

    if not _is_primary_chart_chunk(bundle, chunk):
        logger.info(
            "Skipping duplicate chart chunk clip=%s beat=%s", task.clip_id, beat_id
        )
        return {"clipId": task.clip_id, "skipped": True, "reason": "chart_duplicate"}

    logger.info("Rendering chart clip=%s visual_type=%s", task.clip_id, visual_type)
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


def _find_chunk(bundle, clip_id: str):
    return next(
        (c for c in bundle.chunks.chunks if getattr(c, "id", getattr(c, "beat_id", None)) == clip_id),
        None,
    )


def _is_primary_chart_chunk(bundle, chunk: Optional[Any]) -> bool:
    if chunk is None:
        return True

    chunk_id = getattr(chunk, "id", getattr(chunk, "beat_id", None))
    beat_id = getattr(chunk, "beat_id", None)
    if not chunk_id or not beat_id:
        return True

    if chunk_id == beat_id:
        return True

    siblings = [
        c
        for c in bundle.chunks.chunks
        if getattr(c, "beat_id", None) == beat_id
    ]
    if not siblings:
        return True

    first_chunk_id = getattr(siblings[0], "id", getattr(siblings[0], "beat_id", None))
    return chunk_id == first_chunk_id
