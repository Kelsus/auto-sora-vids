from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class Beat(BaseModel):
    """Single narrative beat destined for a Sora clip."""

    id: str
    purpose: str = Field(description="Narrative purpose, e.g., hook, reveal, resolution")
    transcript: str
    suspense_level: int = Field(ge=1, le=5, description="Relative tension score")
    estimated_duration_sec: float
    visual_seed: Optional[str] = Field(default=None, description="Key visual motif")
    audio_mood: Optional[str] = Field(default=None, description="Music or sound cue guidance")


class ScriptPlan(BaseModel):
    beats: List[Beat]
    premise: str
    controversy_summary: str
    withheld_context: str
    final_reveal: str
