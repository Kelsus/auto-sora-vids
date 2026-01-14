from __future__ import annotations

from pathlib import Path

from aivideomaker.article_ingest.model import ArticleBundle, ArticleDocument, ArticleMetadata
from aivideomaker.chunker.model import Chunk, ChunkPlan
from aivideomaker.chart_planner.models import ChartIdea, ChartPlan
from aivideomaker.media_pipeline.sora_client import SoraClient
from aivideomaker.prompt_builder.builder import MediaPromptBuilder
from aivideomaker.prompt_builder.model import MediaPrompt
from aivideomaker.script_engine.model import Beat, BeatVisualSpec, ScriptPlan
from aivideomaker.user_images.assigner import UserImageAssigner
from aivideomaker.user_images.models import UserImageAsset, UserImagePlan


def test_materialize_user_images_converts_avif_to_jpg(tmp_path, monkeypatch) -> None:
    from aivideomaker.orchestrator import PipelineConfig, PipelineOrchestrator

    src = tmp_path / "input.avif"
    src.write_bytes(b"not a real avif")
    dest_dir = tmp_path / "dest"

    orchestrator = PipelineOrchestrator.default(
        PipelineConfig(
            llm_provider="echo",
            enable_script_review=False,
            require_human_approval=False,
        )
    )

    def fake_run(args, capture_output, text):  # noqa: ANN001
        # args[-1] is output path
        Path(args[-1]).write_bytes(b"\xff\xd8\xff")  # minimal JPEG marker

        class Result:
            returncode = 0
            stderr = ""

        return Result()

    monkeypatch.setattr("subprocess.run", fake_run)
    out = orchestrator._materialize_user_images([src], dest_dir=dest_dir)
    assert len(out) == 1
    assert out[0].suffix == ".jpg"
    assert out[0].exists()


def test_user_image_assigner_coerces_to_still_motion_and_skips_chart_beats() -> None:
    script = ScriptPlan(
        premise="p",
        controversy_summary="c",
        withheld_context="w",
        final_reveal="r",
        beats=[
            Beat(
                id="b1",
                purpose="hook",
                transcript="A massive wildfire spreads across the valley.",
                suspense_level=3,
                estimated_duration_sec=5.0,
                visual=BeatVisualSpec(type="cinematic_broll", macro="docu_environment_wide"),
            ),
            Beat(
                id="b2",
                purpose="numbers",
                transcript="The stock price fell 40% in a week.",
                suspense_level=3,
                estimated_duration_sec=5.0,
                visual=BeatVisualSpec(type="chart"),
            ),
            Beat(
                id="b3",
                purpose="detail",
                transcript="Firefighters arrive at the scene.",
                suspense_level=3,
                estimated_duration_sec=5.0,
            ),
        ],
    )

    plan = UserImagePlan(
        images=[
            UserImageAsset(
                id="u1",
                source_uri="/tmp/u1.png",
                materialized_path="/tmp/u1.png",
                title="Wildfire smoke",
                keywords=["wildfire", "smoke"],
            )
        ]
    )

    assigner = UserImageAssigner()
    updated, assignments = assigner.assign(plan, script, blocked_beats={"b2"})
    assert [(a.image_id, a.beat_id) for a in assignments] == [("u1", "b1")]

    beat1 = next(b for b in updated.beats if b.id == "b1")
    assert beat1.visual is not None
    assert beat1.visual.type == "still_motion"
    assert beat1.visual.macro is None

    beat2 = next(b for b in updated.beats if b.id == "b2")
    assert beat2.visual is not None
    assert beat2.visual.type == "chart"


def test_prompt_builder_attaches_user_image_when_no_chart() -> None:
    doc = ArticleDocument(
        metadata=ArticleMetadata(url="https://example.com/test", title="Test", slug="test"),
        text="t",
    )
    article = ArticleBundle.from_document(doc)

    script = ScriptPlan(
        premise="p",
        controversy_summary="c",
        withheld_context="w",
        final_reveal="r",
        beats=[
            Beat(
                id="b1",
                purpose="hook",
                transcript="Wildfire spreads.",
                suspense_level=3,
                estimated_duration_sec=5.0,
                visual=BeatVisualSpec(type="still_motion"),
            ),
            Beat(
                id="b2",
                purpose="numbers",
                transcript="Numbers go up.",
                suspense_level=3,
                estimated_duration_sec=5.0,
                visual=BeatVisualSpec(type="chart", spec_id="c1"),
            ),
        ],
    )
    chunks = ChunkPlan(
        chunks=[
            Chunk(id="c1", beat_id="b1", transcript="Wildfire spreads.", estimated_duration_sec=5.0),
            Chunk(id="c2", beat_id="b2", transcript="Numbers go up.", estimated_duration_sec=5.0),
        ],
        total_duration_sec=10.0,
    )

    chart_plan = ChartPlan(
        charts=[
            ChartIdea(
                id="c1",
                title="Chart",
                summary="s",
                reason="r",
                image_path="media/charts/chart.png",
                data_points=[],
                keywords=[],
            )
        ]
    )
    chart_assignments = {"b2": "c1"}

    user_image_plan = UserImagePlan(
        images=[
            UserImageAsset(
                id="u1",
                source_uri="local:/tmp/u1.png",
                materialized_path="media/user_images/u1.png",
                title="Wildfire",
                keywords=["wildfire"],
            )
        ]
    )
    user_image_assignments = {"b1": "u1"}

    builder = MediaPromptBuilder()
    bundle = builder.build(
        article,
        script,
        chunks,
        chart_plan=chart_plan,
        chart_assignments=chart_assignments,
        user_image_plan=user_image_plan,
        user_image_assignments=user_image_assignments,
    )
    prompts = {p.chunk_id: p for p in bundle.media_prompts}
    assert prompts["c1"].reference_images == ["media/user_images/u1.png"]
    assert prompts["c2"].reference_images == ["media/charts/chart.png"]


def test_sora_client_resolves_media_user_images_relative_to_run_dir(tmp_path, monkeypatch) -> None:
    run_dir = tmp_path / "run"
    sora_dir = run_dir / "media" / "sora_clips"
    user_images_dir = run_dir / "media" / "user_images"
    sora_dir.mkdir(parents=True)
    user_images_dir.mkdir(parents=True)
    image_path = user_images_dir / "u1.png"
    image_path.write_bytes(b"\x89PNG\r\n\x1a\n")  # minimal marker; method is monkeypatched

    client = SoraClient(asset_dir=sora_dir, api_key="sk-test")
    monkeypatch.setattr(client, "_ensure_reference_dimensions", lambda p: p)
    prompt = MediaPrompt(
        chunk_id="x",
        transcript="t",
        visual_prompt="v",
        audio_prompt="a",
        reference_images=["media/user_images/u1.png"],
    )
    prepared = client._prepare_reference_images(prompt)
    assert prepared == [image_path]
