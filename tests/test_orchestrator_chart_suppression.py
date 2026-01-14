from __future__ import annotations

from pathlib import Path

from aivideomaker.orchestrator import PipelineConfig, PipelineOrchestrator
from aivideomaker.script_engine.model import Beat, BeatVisualSpec


def test_user_images_suppress_chart_components(tmp_path: Path) -> None:
    config = PipelineConfig(
        llm_provider="echo",
        enable_script_review=False,
        require_human_approval=False,
        use_music=False,
        input_images=[tmp_path / "u1.jpg"],
    )
    orchestrator = PipelineOrchestrator.default(config)
    assert orchestrator.chart_planner is None
    assert orchestrator.chart_assigner is None
    assert orchestrator.chart_renderer is None
    assert orchestrator.openai_chart_client is None
    assert orchestrator.gemini_image_client is None


def test_resolve_visual_mode_downgrades_chart_when_images_provided(tmp_path: Path) -> None:
    config = PipelineConfig(
        llm_provider="echo",
        enable_script_review=False,
        require_human_approval=False,
        use_music=False,
        input_images=[tmp_path / "u1.jpg"],
    )
    orchestrator = PipelineOrchestrator.default(config)

    beat = Beat(
        id="b1",
        purpose="numbers",
        transcript="t",
        suspense_level=3,
        estimated_duration_sec=5.0,
        visual=BeatVisualSpec(type="chart"),
    )
    assert orchestrator._resolve_visual_mode(beat) == "cinematic_broll"
