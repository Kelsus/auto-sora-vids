from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class VoiceDirective(BaseModel):
    voice_id: str
    reference_path: Optional[str] = None
    notes: Optional[str] = None


class MediaPrompt(BaseModel):
    chunk_id: str
    transcript: str
    visual_prompt: str
    audio_prompt: str
    duration_sec: float = Field(default=10.0)
    negative_prompt: Optional[str] = None
    cameo_voice: Optional[VoiceDirective] = None
    visual_type: Optional[str] = Field(
        default=None,
        description="Normalized visual.type from the beat (e.g., chart, still_motion, cinematic_broll)",
    )
    render_mode: Optional[str] = Field(
        default=None,
        description="High-level pipeline target for this prompt (e.g., chart_scene, still_scene, sora_clip)",
    )
    chart_spec_id: Optional[str] = None
    chart_variant: Optional[str] = None
    reference_images: List[str] = Field(
        default_factory=list,
        description="Absolute or relative paths to image assets that should accompany the prompt",
    )
    
    # Chart composition workflow fields
    static_scene_prompt: Optional[str] = Field(
        default=None,
        description="Static scene description for Gemini image generation (chart workflow)",
    )
    animation_prompt: Optional[str] = Field(
        default=None,
        description="Sora animation prompt for chart reveal (chart workflow)",
    )
    composite_image_path: Optional[str] = Field(
        default=None,
        description="Path to Gemini-generated composite image (chart workflow)",
    )


class MediaPromptBundle(BaseModel):
    article_slug: str
    media_prompts: List[MediaPrompt]
    voice_session: Optional[str] = None
