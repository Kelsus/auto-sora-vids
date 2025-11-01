"""Prompt transformation utilities for chart composition workflow."""

from __future__ import annotations

import json
import logging
from textwrap import dedent
from typing import Any, Optional

from aivideomaker.script_engine.llm import LLMClient
from aivideomaker.script_engine.utils import load_json_with_repair

logger = logging.getLogger(__name__)


def convert_sora_prompt_to_static_scene(
    original_prompt: str,
    chart_info: dict[str, Any],
    llm: Optional[LLMClient] = None,
) -> str:
    """
    Transform a Sora motion prompt into a static scene description.
    
    Uses Claude to intelligently remove temporal/motion elements while
    preserving spatial, atmospheric, and compositional details.
    
    Args:
        original_prompt: Original Sora prompt with camera movements
        chart_info: Metadata about the chart (variant, title, etc.)
        llm: LLM client (uses Claude if available)
        
    Returns:
        Static scene description suitable for image generation
    """
    if not llm:
        # Fallback: simple rule-based transformation
        return _fallback_static_transform(original_prompt, chart_info)
    
    chart_variant = chart_info.get("variant", "chart")
    chart_title = chart_info.get("title", "")
    
    prompt_text = dedent(
        """
        You are a cinematography expert helping transform video scene descriptions into static image compositions.
        
        TASK: Convert the following Sora video prompt into a static scene description for image generation.
        
        REQUIREMENTS:
        1. REMOVE all motion elements:
           - Camera movements (pan, tilt, zoom, dolly, push, pull, track)
           - Temporal actions (reveals, transitions, evolves)
           - Action verbs (moving, flowing, shifting)
        
        2. PRESERVE and ENHANCE:
           - Spatial composition (foreground/background, placement)
           - Lighting descriptions (dramatic, natural, mood)
           - Atmosphere and setting details
           - Color palette and aesthetic
           - Professional/documentary tone
        
        3. ADD chart integration:
           - Specify WHERE the chart should appear (wall, screen, tablet, desk)
           - Ensure the chart is prominent but natural in the scene
           - Maintain 9:16 portrait aspect ratio for short-form video
           
        4. OUTPUT FORMAT:
           - Return ONLY the transformed scene description
           - 2-3 sentences maximum
           - Professional, descriptive, and clear
           - No markdown, no preamble
        
        ORIGINAL SORA PROMPT:
        {original_prompt}
        
        CHART INFO:
        - Type: {chart_variant}
        - Title: {chart_title}
        
        Transform this into a static scene description:
        """
    ).strip().format(
        original_prompt=original_prompt,
        chart_variant=chart_variant,
        chart_title=chart_title or "data visualization",
    )
    
    try:
        response = llm.complete(
            prompt_text,
            system="You are a visual composition expert specializing in static scene design.",
            temperature=0.3,
            max_tokens=300,
        )
        
        # Clean up response
        static_scene = response.strip()
        
        # Remove any markdown formatting if present
        if static_scene.startswith("```"):
            lines = static_scene.split("\n")
            static_scene = "\n".join(line for line in lines if not line.startswith("```"))
            static_scene = static_scene.strip()
        
        logger.info("Transformed Sora prompt to static scene via Claude")
        return static_scene
        
    except Exception as exc:
        logger.warning("Failed to transform prompt via Claude: %s; using fallback", exc)
        return _fallback_static_transform(original_prompt, chart_info)


def create_chart_animation_prompt(
    chart_info: dict[str, Any],
    original_narration: str = "",
    llm: Optional[LLMClient] = None,
) -> str:
    """
    Create a Sora animation prompt for animating the chart within the scene.
    
    The prompt should ask Sora to craft creative, appropriate animations that match
    the chart type and narrative context. It should tell Sora that the animation should construct or destruct or decorate the chart (choose one) but not change the values in the chart. The chart should continue to communicate the information that it has within it to the viewer. 
    It should also tell Sora to bring alive other elements in the scene contained in the image in a cinematic way consistent with the original narration and with intriguiging documentary filmmaking.
    
    Args:
        chart_info: Metadata about the chart (variant, title, data points)
        original_narration: The narration text for context
        
    Returns:
        Sora animation prompt starting from the composite image
    """
    if not llm:
        # Fallback: generic animation
        return _fallback_animation_prompt(chart_info)
    
    chart_variant = chart_info.get("variant", "bar")
    chart_title = chart_info.get("title", "")
    data_count = len(chart_info.get("data_points", []))
    
    prompt_text = dedent(
        """
        You are a motion graphics expert creating animation directions for Sora video generation.
        
        TASK: Create a Sora animation prompt that will animate a chart within an existing scene image.
        
        CONTEXT:
        - We have a static image showing a chart in a professional scene
        - Sora will start from this image and animate it
        - The animation should reveal/animate the chart data creatively
        
        CHART DETAILS:
        - Type: {chart_variant}
        - Title: {chart_title}
        - Data points: {data_count}
        - Narration context: {narration}
        
        REQUIREMENTS:
        1. START: "Starting from this image, ..."
        2. CAMERA MOVEMENT: Subtle push-in or reveal toward the chart (optional)
        3. CHART ANIMATION: Creative data reveal appropriate to chart type
           - Bar charts: Bars rise from bottom
           - Line charts: Line draws/traces across
           - Pie/Donut: Segments animate in clockwise
           - Area charts: Fill animates upward
        4. TIMING: Describe animation as smooth and professional (5-8 seconds)
        5. MAINTAIN: Professional documentary aesthetic throughout
        
        CONSTRAINTS:
        - No sudden movements or cuts
        - No additional text appearing beyond chart
        - Keep focus on the data reveal
        - Maintain scene atmosphere
        
        OUTPUT: Return ONLY the animation prompt (2-3 sentences, no markdown).
        """
    ).strip().format(
        chart_variant=chart_variant,
        chart_title=chart_title or "data visualization",
        data_count=data_count,
        narration=original_narration[:200] if original_narration else "data insight",
    )
    
    try:
        response = llm.complete(
            prompt_text,
            system="You are a motion graphics expert crafting precise animation directions.",
            temperature=0.7,  # Higher temp for creative animations
            max_tokens=300,
        )
        
        animation_prompt = response.strip()
        
        # Remove markdown if present
        if animation_prompt.startswith("```"):
            lines = animation_prompt.split("\n")
            animation_prompt = "\n".join(line for line in lines if not line.startswith("```"))
            animation_prompt = animation_prompt.strip()
        
        # Ensure it starts appropriately
        if not animation_prompt.lower().startswith("starting from"):
            animation_prompt = f"Starting from this image, {animation_prompt}"
        
        logger.info("Created chart animation prompt via Claude")
        return animation_prompt
        
    except Exception as exc:
        logger.warning("Failed to create animation prompt via Claude: %s; using fallback", exc)
        return _fallback_animation_prompt(chart_info)


# Fallback transformations (rule-based) ------------------------------------


def _fallback_static_transform(original_prompt: str, chart_info: dict[str, Any]) -> str:
    """Simple rule-based transformation when Claude is unavailable."""
    # Remove common motion words
    static = original_prompt
    motion_words = [
        "slow pan", "camera pan", "panning",
        "zoom in", "zoom out", "zooming",
        "push in", "push to", "pushing",
        "pull back", "pull out", "pulling",
        "dolly", "tracking", "reveals",
        "transitions to", "moves to", "moving",
    ]
    
    for word in motion_words:
        static = static.replace(word, "")
    
    # Add static framing
    chart_variant = chart_info.get("variant", "chart")
    static = static.strip()
    
    if not static:
        static = f"Professional office environment with {chart_variant} chart prominently displayed on wall"
    else:
        if "chart" not in static.lower() and chart_variant not in static.lower():
            static += f" with {chart_variant} chart visible on screen"
    
    return static


def _fallback_animation_prompt(chart_info: dict[str, Any]) -> str:
    """Generic animation when Claude is unavailable."""
    variant = chart_info.get("variant", "bar")
    
    animations = {
        "bar": "bars rise smoothly from bottom to their final heights",
        "line": "line traces across from left to right, connecting data points",
        "pie": "segments animate in clockwise, building the complete circle",
        "donut": "ring segments fill in clockwise with smooth transitions",
        "area": "area fills upward from baseline, revealing the data shape",
    }
    
    anim_desc = animations.get(variant, "data elements animate in sequentially")
    
    return (
        f"Starting from this image, camera gently pushes toward the chart. "
        f"The chart animates: {anim_desc}. "
        f"Labels fade in subtly after data appears. Professional, smooth motion throughout."
    )


__all__ = [
    "convert_sora_prompt_to_static_scene",
    "create_chart_animation_prompt",
]

