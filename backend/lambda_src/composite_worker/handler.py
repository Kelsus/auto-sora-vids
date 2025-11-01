from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from job_worker.models import ClipTask
from job_worker.workflow import PipelineWorkflow

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:  # pragma: no cover - thin wrapper
    """
    Unified handler for preprocessing chart and still_motion clips.
    
    For charts: Generates chart PNG, then creates composite scene with Gemini,
    then prepares animation prompt for Sora.
    
    For still_motion: Prepares still images for Sora animation.
    """
    workflow = PipelineWorkflow()
    task = ClipTask.from_payload(event)

    logger.info(
        "Composite worker invoked clip=%s force_preprocess=%s request_id=%s",
        task.clip_id,
        task.force_preprocess,
        getattr(context, "aws_request_id", "unknown"),
    )

    bundle = workflow._bundle_store.load(task.job_context.bundle_key)
    
    # Check render_mode from prompt FIRST (overrides beat visual type)
    prompt = next((p for p in bundle.prompts.media_prompts if p.chunk_id == task.clip_id), None)
    if prompt and getattr(prompt, "render_mode", None) == "sora_clip":
        logger.info("Skipping composite generation clip=%s reason=forced_sora", task.clip_id)
        return {"clipId": task.clip_id, "skipped": True, "reason": "forced_sora"}
    
    visual_type = _resolve_visual_type(bundle, task.clip_id)
    
    # Handle chart workflow
    if visual_type == "chart":
        chunk = _find_chunk(bundle, task.clip_id)
        if not _is_primary_chart_chunk(bundle, chunk):
            beat_id = getattr(chunk, "beat_id", task.clip_id)
            logger.info(
                "Skipping duplicate chart chunk clip=%s beat=%s", 
                task.clip_id, 
                beat_id
            )
            return {"clipId": task.clip_id, "skipped": True, "reason": "chart_duplicate"}
        
        logger.info("Processing chart composition clip=%s", task.clip_id)
        result = workflow.render_clip(task)
        result["mode"] = "chart"
        return result
    
    # Handle still_motion workflow  
    elif visual_type in {"still_motion", "still"}:
        logger.info("Processing still_motion clip=%s", task.clip_id)
        result = workflow.render_clip(task)
        result["mode"] = "still"
        return result
    
    # Skip other visual types
    else:
        logger.info(
            "Skipping non-composite clip=%s visual_type=%s", 
            task.clip_id, 
            visual_type
        )
        return {
            "clipId": task.clip_id, 
            "skipped": True, 
            "reason": f"not_composite_{visual_type}"
        }


def _resolve_visual_type(bundle, clip_id: str) -> str:
    """Determine the visual type for a clip."""
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
    """Find the chunk object for a given clip ID."""
    return next(
        (c for c in bundle.chunks.chunks if getattr(c, "id", getattr(c, "beat_id", None)) == clip_id),
        None,
    )


def _is_primary_chart_chunk(bundle, chunk: Optional[Any]) -> bool:
    """
    Determine if this is the primary chunk for a chart beat.
    Only the first chunk of a beat should render the chart.
    """
    if chunk is None:
        return True

    chunk_id = getattr(chunk, "id", getattr(chunk, "beat_id", None))
    beat_id = getattr(chunk, "beat_id", None)
    if not chunk_id or not beat_id:
        return True

    # If chunk_id matches beat_id, it's the primary chunk
    if chunk_id == beat_id:
        return True

    # Find all sibling chunks for this beat
    siblings = [
        c
        for c in bundle.chunks.chunks
        if getattr(c, "beat_id", None) == beat_id
    ]
    if not siblings:
        return True

    # Check if this is the first chunk
    first_chunk_id = getattr(siblings[0], "id", getattr(siblings[0], "beat_id", None))
    return chunk_id == first_chunk_id

