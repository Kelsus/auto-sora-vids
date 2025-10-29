from __future__ import annotations

import json

from aivideomaker.article_ingest.model import ArticleBundle, ArticleDocument, ArticleMetadata
from aivideomaker.script_engine.engine import ScriptEngine
from aivideomaker.script_engine.llm import EchoLLM


class MissingDurationLLM(EchoLLM):
    def complete(self, prompt: str) -> str:  # pragma: no cover - simple stub
        del prompt
        payload = {
            "premise": "",
            "controversy_summary": "",
            "withheld_context": "",
            "final_reveal": "",
            "beats": [
                {
                    "id": "hook",
                    "purpose": "hook",
                    "transcript": "Short lead to draw viewers in.",
                    "suspense_level": 3,
                }
            ],
        }
        return json.dumps(payload)


def build_article_bundle() -> ArticleBundle:
    metadata = ArticleMetadata(
        url="https://example.com/story",
        title="Example Story",
        source="Example",
        slug="example-story",
    )
    document = ArticleDocument(metadata=metadata, text="Body text")
    return ArticleBundle.from_document(document)


def test_script_engine_fills_missing_estimated_duration() -> None:
    engine = ScriptEngine(llm=MissingDurationLLM())
    bundle = build_article_bundle()
    plan = engine.generate_script(bundle)
    assert plan.beats[0].estimated_duration_sec is not None
    assert plan.beats[0].estimated_duration_sec >= 4.0
