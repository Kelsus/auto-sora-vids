from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class ChartDataPoint(BaseModel):
    label: str
    value: float
    secondary_value: Optional[float] = None
    series: Optional[str] = None


class ChartIdea(BaseModel):
    id: str
    title: str
    summary: str = Field(description="High-level description of what the chart communicates")
    reason: str = Field(description="Why the chart matters in the narrative")
    variant: Optional[str] = Field(default=None, description="Suggested visualization type (bar, line, donut, etc.)")
    subtitle: Optional[str] = None
    note: Optional[str] = None
    x_label: Optional[str] = None
    y_label: Optional[str] = None
    source: Optional[str] = None
    keywords: List[str] = Field(default_factory=list, description="Key terms to match against beats when assigning charts")
    data_points: List[ChartDataPoint] = Field(default_factory=list, description="Structured numeric points for rendering")
    code_prompt: Optional[str] = Field(
        default=None,
        description="Pre-generated prompt for OpenAI code interpreter; optional because we can build it later.",
    )

    def keyword_set(self) -> set[str]:
        return {token.lower() for token in self.keywords if token}


class ChartPlan(BaseModel):
    charts: List[ChartIdea] = Field(default_factory=list)

    def is_empty(self) -> bool:
        return not self.charts

    def summary_lines(self) -> List[str]:
        lines: List[str] = []
        for chart in self.charts:
            lines.append(
                f"- {chart.title}: {chart.summary} (variant={chart.variant or 'unspecified'})"
            )
        return lines


__all__ = ["ChartDataPoint", "ChartIdea", "ChartPlan"]
