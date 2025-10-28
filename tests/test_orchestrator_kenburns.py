from __future__ import annotations

from aivideomaker.orchestrator import PipelineOrchestrator


def test_kenburns_duration_scale_increases_with_length() -> None:
    short = PipelineOrchestrator._kenburns_duration_scale(1.6)
    medium = PipelineOrchestrator._kenburns_duration_scale(3.0)
    long = PipelineOrchestrator._kenburns_duration_scale(5.0)

    assert 0.0 < short < medium < long <= 1.0


def test_kenburns_progress_respects_holds() -> None:
    total = 4.0
    hold_start = 0.1
    hold_end = 0.2

    early = PipelineOrchestrator._kenburns_progress(0.05 * total, total, hold_start=hold_start, hold_end=hold_end)
    middle = PipelineOrchestrator._kenburns_progress(0.5 * total, total, hold_start=hold_start, hold_end=hold_end)
    late = PipelineOrchestrator._kenburns_progress(0.9 * total, total, hold_start=hold_start, hold_end=hold_end)

    assert early == 0.0
    assert 0.0 < middle < 1.0
    assert late == 1.0


def test_kenburns_motion_spec_tapers_motion_for_short_clips() -> None:
    pattern = PipelineOrchestrator._KENBURNS_PATTERNS[0]

    short = PipelineOrchestrator._kenburns_motion_spec(pattern, duration=1.6)
    long = PipelineOrchestrator._kenburns_motion_spec(pattern, duration=4.8)

    assert short["end_scale"] < long["end_scale"]
    assert short["hold_start"] == long["hold_start"]
