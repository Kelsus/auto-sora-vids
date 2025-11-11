from __future__ import annotations

import json

from aivideomaker.article_ingest.model import ArticleBundle, ArticleDocument, ArticleMetadata
from aivideomaker.script_engine.directives import get_length_profile, get_style_directive
from aivideomaker.script_engine.engine import ScriptEngine
from aivideomaker.script_engine.llm import EchoLLM
from aivideomaker.script_engine.prompts import render_planning_prompt


class MultiBeatLLM(EchoLLM):
    def complete(self, prompt: str) -> str:  # pragma: no cover - behavior verified via ScriptPlan outputs
        del prompt
        beats = []
        for index in range(5):
            beats.append(
                {
                    "id": f"beat_{index + 1}",
                    "purpose": "info",
                    "transcript": "One detailed sentence with plenty of context to split.",
                    "suspense_level": 3,
                    "estimated_duration_sec": 18,
                }
            )
        payload = {
            "premise": "",
            "controversy_summary": "",
            "withheld_context": "",
            "final_reveal": "",
            "beats": beats,
        }
        return json.dumps(payload)


def build_article_bundle() -> ArticleBundle:
    metadata = ArticleMetadata(
        url="https://example.com/story",
        title="Example Story",
        source="Example",
        slug="example-story",
        byline="Reporter",
        published_at="2024-01-01",
    )
    document = ArticleDocument(metadata=metadata, text="Example body text")
    return ArticleBundle.from_document(document)


def test_render_planning_prompt_injects_style_and_runtime() -> None:
    article = build_article_bundle()
    directive = get_style_directive("how_to")
    profile = get_length_profile("15s")
    prompt = render_planning_prompt(
        article,
        style_directive=directive,
        length_profile=profile,
    )
    assert "How-To Explainer" in prompt
    assert "Target runtime: ~15 seconds" in prompt
    assert "Label beats sequentially" in prompt


def test_script_engine_normalizes_short_profile() -> None:
    engine = ScriptEngine(llm=MultiBeatLLM())
    article = build_article_bundle()
    profile = get_length_profile("15s")
    directive = get_style_directive("how_to")
    plan = engine.generate_script(
        article,
        style_directive=directive,
        length_profile=profile,
    )
    assert plan.target_runtime_sec == profile.target_runtime_sec
    assert plan.target_beat_count == profile.target_beat_count
    assert plan.narrative_style == directive.style_id
    assert len(plan.beats) == profile.target_beat_count
    total_duration = sum(beat.estimated_duration_sec for beat in plan.beats)
    assert abs(total_duration - profile.target_runtime_sec) < 0.6
    for beat in plan.beats:
        assert profile.min_beat_duration_sec <= beat.estimated_duration_sec <= profile.max_beat_duration_sec


def test_first_person_directive_available() -> None:
    directive = get_style_directive("first_person")
    block = directive.prompt_block()
    assert directive.style_id == "first_person"
    assert "First Person Story" in block
    assert "first-person" in block or "first person" in block.lower()
