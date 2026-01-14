from __future__ import annotations

from aivideomaker.article_ingest.model import ArticleBundle, ArticleDocument, ArticleMetadata
from aivideomaker.chunker.model import Chunk, ChunkPlan
from aivideomaker.prompt_builder.builder import MediaPromptBuilder
from aivideomaker.script_engine.model import Beat, BeatVisualSpec, BeatQCRules, ScriptPlan


def build_article() -> ArticleBundle:
    doc = ArticleDocument(
        metadata=ArticleMetadata(url="https://example.com/test", title="Test", slug="test"),
        text="t",
    )
    return ArticleBundle.from_document(doc)


def test_visual_prompt_includes_common_sense_motion_rules() -> None:
    article = build_article()
    script = ScriptPlan(
        premise="p",
        controversy_summary="c",
        withheld_context="w",
        final_reveal="r",
        beats=[
            Beat(
                id="b1",
                purpose="hook",
                transcript="A close-up of a person speaking.",
                suspense_level=3,
                estimated_duration_sec=5.0,
                qc=BeatQCRules(allow_text=False, allow_numbers=False, allow_split_screen=False),
                visual=BeatVisualSpec(type="cinematic_broll"),
            ),
        ],
    )
    chunks = ChunkPlan(
        chunks=[Chunk(id="c1", beat_id="b1", transcript="t", estimated_duration_sec=5.0)],
        total_duration_sec=5.0,
    )
    builder = MediaPromptBuilder()
    bundle = builder.build(article, script, chunks)
    prompt = bundle.media_prompts[0]
    assert "Common-sense physical realism" in prompt.visual_prompt
    assert prompt.negative_prompt is not None
    assert "impossible physics" in prompt.negative_prompt
    assert "time reversal" in prompt.negative_prompt
    assert "duplicated parts" in prompt.negative_prompt


def test_timepiece_rules_added_when_watch_is_mentioned() -> None:
    article = build_article()
    script = ScriptPlan(
        premise="p",
        controversy_summary="c",
        withheld_context="w",
        final_reveal="r",
        beats=[
            Beat(
                id="b1",
                purpose="detail",
                transcript="A macro shot of a watch dial; the second hand ticks.",
                suspense_level=3,
                estimated_duration_sec=5.0,
                qc=BeatQCRules(allow_text=True, allow_numbers=True, allow_split_screen=False),
                visual=BeatVisualSpec(type="cinematic_broll"),
            ),
        ],
    )
    chunks = ChunkPlan(
        chunks=[Chunk(id="c1", beat_id="b1", transcript="t", estimated_duration_sec=5.0)],
        total_duration_sec=5.0,
    )
    builder = MediaPromptBuilder()
    bundle = builder.build(article, script, chunks)
    prompt = bundle.media_prompts[0]
    assert "clockwise" in prompt.visual_prompt.lower()
    assert prompt.negative_prompt is not None
    assert "counterclockwise clock hands" in prompt.negative_prompt
    assert "double second hand" in prompt.negative_prompt
