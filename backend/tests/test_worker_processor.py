from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import SimpleNamespace

from infra.lambda_src.job_worker.config import WorkerSettings
from infra.lambda_src.job_worker.models import ClipTask, JobContext, JobMetadata, JobStatusUpdate
from infra.lambda_src.job_worker.workflow import PipelineWorkflow


class FakeBundle:
    def __init__(self, slug: str, clip_ids: list[str], sora_assets: list[Path] | None = None, final_video: Path | None = None):
        self.article = SimpleNamespace(slug=slug)
        self.prompts = SimpleNamespace(media_prompts=[SimpleNamespace(chunk_id=cid) for cid in clip_ids])
        self.sora_assets = sora_assets or []
        self.final_video = final_video

    def model_dump(self, mode: str = "json"):
        return {
            "article": {"slug": self.article.slug},
            "prompts": [prompt.chunk_id for prompt in self.prompts.media_prompts],
            "sora_assets": [str(path) for path in self.sora_assets],
            "final_video": str(self.final_video) if self.final_video else None,
        }

    def model_copy(self, update=None):
        copy = FakeBundle(
            slug=self.article.slug,
            clip_ids=[prompt.chunk_id for prompt in self.prompts.media_prompts],
            sora_assets=list(self.sora_assets),
            final_video=self.final_video,
        )
        if update:
            for key, value in update.items():
                setattr(copy, key, value)
        return copy


class StubRunner:
    def __init__(self, tmp_path: Path) -> None:
        self.tmp_path = tmp_path
        self.prompts_called_with: list[str] = []
        self.render_calls: list[str] = []
        self.stitch_calls: list[str] = []
        self.bundle = FakeBundle("story", ["clip-1", "clip-2"])

    def run_prompts(self, article_url: str, dry_run: bool):
        self.prompts_called_with.append(article_url)
        return SimpleNamespace(bundle=self.bundle, clip_ids=[prompt.chunk_id for prompt in self.bundle.prompts.media_prompts])

    def render_clip(self, bundle: FakeBundle, clip_id: str, dry_run: bool):
        self.render_calls.append(clip_id)
        clip_path = self.tmp_path / bundle.article.slug / "media" / "sora_clips" / f"{clip_id}.mp4"
        clip_path.parent.mkdir(parents=True, exist_ok=True)
        clip_path.write_text("clip")
        run_dir = self.tmp_path / bundle.article.slug
        relative = clip_path.relative_to(run_dir)
        updated_assets = list(bundle.sora_assets) + [relative]
        updated_bundle = bundle.model_copy(update={"sora_assets": updated_assets})
        return SimpleNamespace(bundle=updated_bundle, clip_asset=clip_path)

    def stitch_final(self, bundle: FakeBundle, dry_run: bool) -> FakeBundle:
        self.stitch_calls.append(bundle.article.slug)
        final_path = self.tmp_path / bundle.article.slug / "exports" / f"{bundle.article.slug}.mp4"
        final_path.parent.mkdir(parents=True, exist_ok=True)
        final_path.write_text("video")
        return FakeBundle(bundle.article.slug, [p.chunk_id for p in bundle.prompts.media_prompts], sora_assets=bundle.sora_assets, final_video=final_path)


class RecordingStorage:
    def __init__(self, snapshot_root: Path) -> None:
        self.uploaded_dirs: list[tuple[str, str]] = []
        self.uploaded_files: list[tuple[str, str]] = []
        self.snapshots: dict[str, Path] = {}
        self.snapshot_root = snapshot_root

    def upload_directory(self, base_path: Path, prefix: str):
        self.uploaded_dirs.append((str(base_path), prefix))
        snapshot_dir = self.snapshot_root / prefix.replace("/", "_")
        if snapshot_dir.exists():
            shutil.rmtree(snapshot_dir)
        shutil.copytree(base_path, snapshot_dir)
        self.snapshots[prefix] = snapshot_dir
        return []

    def upload_file(self, path: Path, key: str):
        self.uploaded_files.append((str(path), key))

    def download_prefix(self, prefix: str, target_dir: Path):
        src = self.snapshots.get(prefix)
        if not src:
            return
        if target_dir.exists():
            shutil.rmtree(target_dir)
        shutil.copytree(src, target_dir)


class RecordingBundleStore:
    def __init__(self) -> None:
        self.saved: dict[str, FakeBundle] = {}

    def save(self, key: str, bundle: FakeBundle) -> None:
        self.saved[key] = bundle

    def load(self, key: str) -> FakeBundle:
        return self.saved[key]


class RecordingRepository:
    def __init__(self) -> None:
        self.updates: list[tuple[str, JobStatusUpdate]] = []

    def update_status(self, job_id: str, update: JobStatusUpdate) -> None:
        self.updates.append((job_id, update))


def build_settings(tmp_path: Path) -> WorkerSettings:
    return WorkerSettings(
        jobs_table_name="table",
        output_bucket="bucket",
        data_root=tmp_path,
        default_dry_run=False,
        final_video_prefix="jobs/final",
        pipeline_config_path=None,
    )


def test_generate_prompts_returns_context_and_updates_status(tmp_path):
    settings = build_settings(tmp_path)
    runner = StubRunner(tmp_path)
    storage = RecordingStorage(tmp_path / "snapshots")
    store = RecordingBundleStore()
    repo = RecordingRepository()
    workflow = PipelineWorkflow(settings=settings, repository=repo, storage=storage, bundle_store=store, runner=runner)

    metadata = JobMetadata(job_id="story", article_url="https://example.com/story")
    context = workflow.generate_prompts(metadata)

    assert context.job_id == "story"
    assert context.clip_ids == ["clip-1", "clip-2"]
    assert repo.updates[0][1].status == "RUNNING"
    assert storage.uploaded_dirs
    assert "story" in storage.uploaded_dirs[0][0]
    assert store.saved[settings.bundle_key("story")] is runner.bundle


def test_render_clip_updates_bundle_and_storage(tmp_path):
    settings = build_settings(tmp_path)
    runner = StubRunner(tmp_path)
    storage = RecordingStorage(tmp_path / "snapshots")
    store = RecordingBundleStore()
    repo = RecordingRepository()
    workflow = PipelineWorkflow(settings=settings, repository=repo, storage=storage, bundle_store=store, runner=runner)

    metadata = JobMetadata(job_id="story", article_url="https://example.com/story")
    context = workflow.generate_prompts(metadata)

    clip_task = ClipTask(job_context=context, clip_id="clip-1")
    result = workflow.render_clip(clip_task)

    assert result["clipId"] == "clip-1"
    saved_bundle = store.saved[context.bundle_key]
    assert any(Path(asset).name == "clip-1.mp4" for asset in saved_bundle.sora_assets)
    assert storage.uploaded_dirs[-1][1] == settings.run_prefix("story")


def test_stitch_final_uploads_video_and_completes(tmp_path):
    settings = build_settings(tmp_path)
    runner = StubRunner(tmp_path)
    storage = RecordingStorage(tmp_path / "snapshots")
    store = RecordingBundleStore()
    repo = RecordingRepository()
    workflow = PipelineWorkflow(settings=settings, repository=repo, storage=storage, bundle_store=store, runner=runner)

    metadata = JobMetadata(job_id="story", article_url="https://example.com/story")
    context = workflow.generate_prompts(metadata)
    workflow.render_clip(ClipTask(job_context=context, clip_id="clip-1"))
    workflow.render_clip(ClipTask(job_context=context, clip_id="clip-2"))

    result = workflow.stitch_final(context)

    assert repo.updates[-1][1].status == "COMPLETED"
    assert storage.uploaded_files  # final video uploaded
    assert result["finalVideoKey"].startswith(settings.final_video_prefix)


def test_mark_failed_records_status(tmp_path):
    settings = build_settings(tmp_path)
    runner = StubRunner(tmp_path)
    storage = RecordingStorage(tmp_path / "snapshots")
    store = RecordingBundleStore()
    repo = RecordingRepository()
    workflow = PipelineWorkflow(settings=settings, repository=repo, storage=storage, bundle_store=store, runner=runner)

    metadata = JobMetadata(job_id="story", article_url="https://example.com/story")
    context = workflow.generate_prompts(metadata)

    workflow.mark_failed(context, error={"message": "boom"})

    assert repo.updates[-1][1].status == "FAILED"
    assert "error_message" in repo.updates[-1][1].attributes
