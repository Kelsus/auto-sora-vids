from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field, computed_field


class BeatVisualSpec(BaseModel):
    type: str
    macro: Optional[str] = Field(default=None, description="Reference to a shot preset or macro")
    spec_id: Optional[str] = Field(default=None, description="Chart specification identifier when applicable")
    negations: List[str] = Field(default_factory=list, description="Negative prompt clauses")


class BeatQCRules(BaseModel):
    allow_text: bool = True
    allow_numbers: bool = True
    numbers_source: Optional[str] = Field(default=None, description="Source identifier required when numbers appear")
    allow_split_screen: bool = False


class Beat(BaseModel):
    """Single narrative beat destined for a Sora clip."""

    id: str
    purpose: str = Field(description="Narrative purpose, e.g., hook, reveal, resolution")
    transcript: str
    suspense_level: int = Field(ge=1, le=5, description="Relative tension score")
    estimated_duration_sec: float
    visual_seed: Optional[str] = Field(default=None, description="Key visual motif")
    audio_mood: Optional[str] = Field(default=None, description="Music or sound cue guidance")
    intent: Optional[str] = Field(default=None, description="High level storytelling intent for the beat")
    visual: Optional[BeatVisualSpec] = Field(default=None, description="Structured visual guidance for downstream generators")
    qc: Optional[BeatQCRules] = Field(default=None, description="Per-beat quality constraints")
    caption_region: Optional[str] = Field(default=None, description="Preferred on-screen caption region")
    min_duration_sec: Optional[float] = Field(default=None, description="Minimum duration guardrail for this beat")


class ScriptPlan(BaseModel):
    beats: List[Beat]
    premise: str
    controversy_summary: str
    withheld_context: str
    final_reveal: str
    social_caption: Optional["SocialCaption"] = None

    @computed_field
    @property
    def full_transcript(self) -> str:
        """Canonical narration assembled from beat transcripts."""
        lines: list[str] = []
        for beat in self.beats:
            text = beat.transcript.strip()
            if text:
                lines.append(text)
        return "\n\n".join(lines)


class SocialCaption(BaseModel):
    description: str
    hashtags: List[str] = Field(default_factory=list)
