from __future__ import annotations

from typing import Any, List, Optional

from pydantic import BaseModel, Field, computed_field


class BeatVisualSpec(BaseModel):
    type: str
    macro: Optional[str] = Field(default=None, description="Reference to a shot preset or macro")
    spec_id: Optional[str] = Field(default=None, description="Chart specification identifier when applicable")
    negations: List[str] = Field(default_factory=list, description="Negative prompt clauses")
    chart_variant: Optional[str] = Field(default=None, description="LLM-suggested chart variant")
    chart_reason: Optional[str] = Field(default=None, description="Narrative justification for the chart choice")
    chart_data_available: Optional[bool] = Field(default=None, description="Whether sufficient labeled data is available to render the chart")
    chart_should_render: Optional[bool] = Field(default=None, description="Whether the chart should be rendered after considering duplicates and clarity")
    chart_duplicates_previous: Optional[bool] = Field(default=None, description="If true, this chart duplicates a prior beat's visualization")
    chart_title: Optional[str] = Field(default=None, description="Display title for the chart")
    chart_subtitle: Optional[str] = Field(default=None, description="Subtitle or supporting line for the chart")
    chart_x_label: Optional[str] = Field(default=None, description="Label for the chart's X axis")
    chart_y_label: Optional[str] = Field(default=None, description="Label for the chart's Y axis")
    chart_note: Optional[str] = Field(default=None, description="Supplementary caption or footnote for the chart")
    chart_series: Optional[List[dict[str, Any]]] = Field(default=None, description="Structured data points for chart rendering")
    still_focus: Optional[str] = Field(default=None, description="Suggested focal subject for still-motion beats")
    still_reason: Optional[str] = Field(default=None, description="Narrative justification for still-motion choice")


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
