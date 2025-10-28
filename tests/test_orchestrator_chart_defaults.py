from aivideomaker.orchestrator import PipelineConfig, PipelineOrchestrator
from aivideomaker.script_engine.model import BeatVisualSpec


def build_orchestrator() -> PipelineOrchestrator:
    config = PipelineConfig(
        llm_provider="echo",
        enable_script_review=False,
        require_human_approval=False,
    )
    return PipelineOrchestrator.default(config)


def test_default_chart_spec_id_uses_style_template():
    orchestrator = build_orchestrator()
    spec_id = orchestrator._default_chart_spec_id()
    assert spec_id == "sample_donut"


def test_ensure_visual_defaults_assigns_chart_spec():
    orchestrator = build_orchestrator()
    visual = BeatVisualSpec(type="chart")
    updated = orchestrator._ensure_visual_defaults(visual, {"chart": {"variant": "donut"}})
    assert updated.spec_id == "sample_donut"


def test_ensure_visual_defaults_respects_existing_spec():
    orchestrator = build_orchestrator()
    visual = BeatVisualSpec(type="chart", spec_id="custom_spec")
    updated = orchestrator._ensure_visual_defaults(visual, {})
    assert updated.spec_id == "custom_spec"


def test_merge_visual_metadata_enriches_visual():
    orchestrator = build_orchestrator()
    visual = BeatVisualSpec(type="chart")
    metadata = {
        "chart": {
            "variant": "donut",
            "reason": "quantitative comparison",
            "data_available": True,
            "should_render": True,
            "duplicates_previous": False,
            "title": "Impact overview",
            "subtitle": "Executives surveyed",
            "x_label": "Group",
            "y_label": "Share",
            "note": "Source: sample study",
            "data_points": [
                {"label": "A", "value": 60},
                {"label": "B", "value": 40},
            ],
        },
        "still_motion": {
            "focus": "quote cards",
            "reason": "qualitative insight",
        },
    }
    updated = orchestrator._merge_visual_metadata(visual, metadata)
    assert updated.chart_variant == "donut"
    assert updated.chart_reason == "quantitative comparison"
    assert updated.chart_data_available is True
    assert updated.chart_should_render is True
    assert updated.chart_duplicates_previous is False
    assert updated.chart_title == "Impact overview"
    assert updated.chart_subtitle == "Executives surveyed"
    assert updated.chart_x_label == "Group"
    assert updated.chart_y_label == "Share"
    assert updated.chart_note == "Source: sample study"
    assert updated.chart_series == [
        {"label": "A", "value": 60},
        {"label": "B", "value": 40},
    ]
    assert updated.still_focus == "quote cards"
    assert updated.still_reason == "qualitative insight"


def test_spec_id_for_variant_matches_known_chart_types():
    orchestrator = build_orchestrator()
    assert orchestrator._spec_id_for_variant("bar") == "sample_bar"
    assert orchestrator._spec_id_for_variant("line") == "sample_line"
    assert orchestrator._spec_id_for_variant("area") == "sample_line"
