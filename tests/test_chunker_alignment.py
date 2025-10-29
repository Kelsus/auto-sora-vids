from __future__ import annotations

import math

from aivideomaker.chunker.planner import ChunkPlanner
from aivideomaker.script_engine.model import Beat, BeatVisualSpec, ScriptPlan


def _alignment_for(text: str, step: float = 0.45) -> dict:
    characters = list(text)
    starts = [round(i * step, 3) for i in range(len(characters))]
    ends = [start + step for start in starts]
    return {
        "alignment": {
            "characters": characters,
            "character_start_times_seconds": starts,
            "character_end_times_seconds": ends,
        }
    }


def test_plan_splits_long_chart_beats_using_alignment() -> None:
    transcript = (
        "This chart-heavy beat carries on well past twelve seconds so the "
        "planner must split it into smaller alignment-driven segments to "
        "stay within Sora clip limits without dropping narration tails."
    )
    beat = Beat(
        id="chart",
        purpose="hook",
        transcript=transcript,
        suspense_level=3,
        estimated_duration_sec=18.0,
        visual=BeatVisualSpec(type="chart"),
    )
    script = ScriptPlan(
        beats=[beat],
        premise="",
        controversy_summary="",
        withheld_context="",
        final_reveal="",
    )

    planner = ChunkPlanner()
    alignment = _alignment_for(script.full_transcript)
    plan = planner.plan(script, alignment=alignment)

    assert len(plan.chunks) > 1, "chart beats should be broken into multiple chunks"
    assert all(chunk.beat_id == "chart" for chunk in plan.chunks)

    max_duration = max(chunk.end_time_sec - chunk.start_time_sec for chunk in plan.chunks)
    assert math.isclose(max_duration, 12.0, rel_tol=1e-3) or max_duration < 12.0 + 1e-6

    end_of_chunks = max(chunk.end_time_sec for chunk in plan.chunks)
    assert end_of_chunks > 15.0, "final chunk should reach the tail of the alignment"
