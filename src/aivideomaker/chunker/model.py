from __future__ import annotations

from typing import List

from pydantic import BaseModel, Field


class Chunk(BaseModel):
    id: str
    beat_id: str
    transcript: str
    estimated_duration_sec: float
    start_time_sec: float = Field(default=0.0)
    end_time_sec: float = Field(default=0.0)
    sora_prompt: str | None = Field(default=None, description="Serialized Sora prompt")


class ChunkPlan(BaseModel):
    chunks: List[Chunk]
    total_duration_sec: float
