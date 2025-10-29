import json

from aivideomaker.media_pipeline.chart_ai_prompt import (
    ChartCodeSpec,
    build_chart_codegen_prompt,
    build_chart_codegen_spec,
)
from aivideomaker.script_engine.model import Beat, BeatVisualSpec


def make_chart_beat() -> Beat:
    visual = BeatVisualSpec(
        type="chart",
        chart_variant="bar",
        chart_title="Headcount impact",
        chart_reason="Comparing reductions",
        chart_x_label="Company size",
        chart_y_label="% junior roles cut",
        chart_note="Source: BSI study",
        chart_series=[
            {"label": "Large firms", "value": 50},
            {"label": "SMBs", "value": 25},
        ],
    )
    return Beat(
        id="impact",
        purpose="Show workforce reductions",
        transcript="Large firms cut half of junior roles while small businesses cut roughly a quarter.",
        suspense_level=3,
        estimated_duration_sec=8.0,
        visual=visual,
    )


def test_build_chart_codegen_spec_extracts_data():
    beat = make_chart_beat()
    spec = build_chart_codegen_spec(beat)
    assert spec.variant == "bar"
    assert spec.title == "Headcount impact"
    assert len(spec.data_points) == 2
    assert spec.data_points[0].label == "Large firms"
    assert spec.data_points[0].value == 50


def test_build_chart_codegen_prompt_contains_payload():
    beat = make_chart_beat()
    spec = build_chart_codegen_spec(beat)
    prompt = build_chart_codegen_prompt(spec)
    payload_start = prompt.split("Chart specification JSON follows:\n", 1)[1]
    payload = json.loads(payload_start.strip())
    assert payload["variant"] == "bar"
    assert payload["data_points"][1]["label"] == "SMBs"
