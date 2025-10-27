from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

root_dir = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root_dir / "backend" / "lambda_src"))
sys.path.insert(0, str(root_dir / "backend" / "lambda_src" / "common_layer" / "python"))

from job_worker.config import WorkerSettings
from job_worker.models import ClipTask, JobContext, JobMetadata, JobStatusUpdate
from job_worker.workflow import PipelineWorkflow
from job_worker import handler as worker_handler


class FakeBundle:
    def __init__(
        self,
        slug: str,
        clip_ids: list[str],
        sora_assets: list[Path] | None = None,
        final_video: Path | None = None,
        captions_ass_path: Path | None = None,
        narration_alignment_payload: dict | None = None,
    ):
        self.article = SimpleNamespace(slug=slug)
        self.prompts = SimpleNamespace(media_prompts=[SimpleNamespace(chunk_id=cid) for cid in clip_ids])
        self.sora_assets = sora_assets or []
        self.final_video = final_video
        self.captions_ass_path = captions_ass_path
        self.narration_alignment_payload = narration_alignment_payload
        self.script = SimpleNamespace(full_transcript="")
        self.chunks = SimpleNamespace(chunks=[])

    def model_dump(self, mode: str = "json"):
        return {
            "article": {"slug": self.article.slug},
            "prompts": [prompt.chunk_id for prompt in self.prompts.media_prompts],
            "sora_assets": [str(path) for path in self.sora_assets],
            "final_video": str(self.final_video) if self.final_video else None,
            "captions_ass_path": str(self.captions_ass_path) if self.captions_ass_path else None,
            "narration_alignment_payload": self.narration_alignment_payload,
        }

    def model_copy(self, update=None):
        copy = FakeBundle(
            slug=self.article.slug,
            clip_ids=[prompt.chunk_id for prompt in self.prompts.media_prompts],
            sora_assets=list(self.sora_assets),
            final_video=self.final_video,
            captions_ass_path=self.captions_ass_path,
            narration_alignment_payload=self.narration_alignment_payload,
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
        return FakeBundle(
            bundle.article.slug,
            [p.chunk_id for p in bundle.prompts.media_prompts],
            sora_assets=bundle.sora_assets,
            final_video=final_path,
            captions_ass_path=bundle.captions_ass_path,
            narration_alignment_payload=bundle.narration_alignment_payload,
        )


class RecordingStorage:
    def __init__(self, snapshot_root: Path) -> None:
        self.uploaded_dirs: list[tuple[str, str]] = []
        self.uploaded_files: list[tuple[str, str, dict[str, str]]] = []
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

    def upload_file(self, path: Path, key: str, metadata: dict[str, str] | None = None, content_type: str | None = None):
        self.uploaded_files.append((str(path), key, metadata or {}, content_type))

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
        self.records: dict[str, dict] = {}
        self.transitions: list[tuple[str, str | None]] = []

    def update_status(self, job_id: str, update: JobStatusUpdate) -> None:
        self.updates.append((job_id, update))
        record = self.records.setdefault(job_id, {})
        record["status"] = update.status
        record.update(update.attributes)
        self.records[job_id] = record

    def fetch(self, job_id: str):
        return self.records.get(job_id)

    def transition_to_running(self, job_id: str) -> bool:
        record = self.records.get(job_id, {})
        current_status = record.get("status")
        self.transitions.append((job_id, current_status))
        if current_status == "QUEUED":
            record["status"] = "RUNNING"
            self.records[job_id] = record
            return True
        return False


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


def test_generate_prompts_includes_pipeline_config_override(tmp_path):
    settings = build_settings(tmp_path)
    runner = StubRunner(tmp_path)
    storage = RecordingStorage(tmp_path / "snapshots")
    store = RecordingBundleStore()
    repo = RecordingRepository()
    workflow = PipelineWorkflow(settings=settings, repository=repo, storage=storage, bundle_store=store, runner=runner)

    override = {"media_provider": "veo", "veo_aspect_ratio": "1:1"}
    metadata = JobMetadata(
        job_id="story",
        article_url="https://example.com/story",
        metadata={"pipeline_config": override},
        pipeline_config=override,
    )

    context = workflow.generate_prompts(metadata)

    assert context.pipeline_config == override
    payload = context.to_payload()
    assert payload["pipelineConfig"] == override
    restored = JobContext.from_payload(payload)
    assert restored.pipeline_config == override


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
    repo.records["story"] = {"metadata": {"pipeline_config": {"drive_folder": "folder-123"}}}
    workflow = PipelineWorkflow(settings=settings, repository=repo, storage=storage, bundle_store=store, runner=runner)

    metadata = JobMetadata(job_id="story", article_url="https://example.com/story", pipeline_config={"drive_folder": "folder-123"})
    context = workflow.generate_prompts(metadata)
    workflow.render_clip(ClipTask(job_context=context, clip_id="clip-1"))
    workflow.render_clip(ClipTask(job_context=context, clip_id="clip-2"))

    result = workflow.stitch_final(context)

    assert repo.updates[-1][1].status == "RUNNING"
    assert storage.uploaded_files  # final video uploaded
    uploaded_metadata = storage.uploaded_files[-1][2]
    assert uploaded_metadata.get("job-id") == "story"
    assert uploaded_metadata.get("drive-folder") == "folder-123"
    assert result["finalVideoKey"].startswith(settings.final_video_prefix)


def test_generate_captions_skips_without_alignment(tmp_path):
    settings = build_settings(tmp_path)
    runner = StubRunner(tmp_path)
    storage = RecordingStorage(tmp_path / "snapshots")
    store = RecordingBundleStore()
    repo = RecordingRepository()
    workflow = PipelineWorkflow(settings=settings, repository=repo, storage=storage, bundle_store=store, runner=runner)

    metadata = JobMetadata(job_id="story", article_url="https://example.com/story")
    context = workflow.generate_prompts(metadata)

    result = workflow.generate_captions(context)

    assert result["status"] == "SKIPPED"


def test_generate_captions_updates_status_with_alignment(tmp_path, monkeypatch):
    settings = build_settings(tmp_path)
    runner = StubRunner(tmp_path)
    runner.bundle.narration_alignment_payload = {"alignment": {"characters": ["a"], "character_start_times_seconds": [0.0], "character_end_times_seconds": [0.5]}}
    runner.bundle.script = SimpleNamespace(full_transcript="a")
    storage = RecordingStorage(tmp_path / "snapshots")
    store = RecordingBundleStore()
    repo = RecordingRepository()
    workflow = PipelineWorkflow(settings=settings, repository=repo, storage=storage, bundle_store=store, runner=runner)

    metadata = JobMetadata(job_id="story", article_url="https://example.com/story")
    context = workflow.generate_prompts(metadata)

    workflow.render_clip(ClipTask(job_context=context, clip_id="clip-1"))
    workflow.render_clip(ClipTask(job_context=context, clip_id="clip-2"))
    workflow.stitch_final(context)

    def fake_write_karaoke_ass(**kwargs):
        export_dir = kwargs["export_dir"]
        captions_path = export_dir / "captions.ass"
        captions_path.parent.mkdir(parents=True, exist_ok=True)
        captions_path.write_text("dummy")
        return captions_path

    monkeypatch.setattr("job_worker.workflow.write_karaoke_ass", fake_write_karaoke_ass)

    monkeypatch.setattr("job_worker.workflow.PipelineWorkflow._burn_captions_into_video", lambda *args, **kwargs: None)

    result = workflow.generate_captions(context)

    assert result["captionsAssPath"].endswith("captions.ass")
    assert repo.updates[-1][1].status == "COMPLETED"
    latest_attrs = repo.updates[-1][1].attributes
    assert latest_attrs["captions_ass_key"].endswith("captions.ass")
    assert "error_message" in latest_attrs and latest_attrs["error_message"] is None


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


def test_mark_failed_handler_updates_original_job(tmp_path, monkeypatch):
    settings = build_settings(tmp_path)
    runner = StubRunner(tmp_path)
    storage = RecordingStorage(tmp_path / "snapshots")
    store = RecordingBundleStore()
    repo = RecordingRepository()
    original_job_id = "https-www-supplychaindive-com-news-old-dominion-freight-line-gri-nearly-5-percen"
    repo.records[original_job_id] = {"status": "RUNNING"}
    workflow = PipelineWorkflow(settings=settings, repository=repo, storage=storage, bundle_store=store, runner=runner)

    monkeypatch.setattr(worker_handler, "PipelineWorkflow", lambda: workflow)

    event = {
        "job": {
            "jobId": original_job_id,
            "articleUrl": "https://www.supplychaindive.com/news/old-dominion-freight-line-gri-nearly-5-percent-nov-2025/803427/",
            "scheduledDatetime": "2025-10-27T13:15:35.343344+00:00",
            "metadata": {
                "pipeline_config": {
                    "drive_folder": "Korsair",
                },
            },
            "jobType": "IMMEDIATE",
        },
        "jobContext": {
            "jobId": original_job_id,
            "articleUrl": "https://www.supplychaindive.com/news/old-dominion-freight-line-gri-nearly-5-percent-nov-2025/803427/",
            "bundleKey": f"jobs/{original_job_id}/bundle.json",
            "outputPrefix": f"jobs/{original_job_id}/run",
            "clipIds": [
                "hook-1",
                "hook-2",
                "hook-3",
                "hook-4",
                "hook-5",
                "hook-6",
                "escalation-1",
                "escalation-2",
                "escalation-3",
                "escalation-4",
                "deepen-1",
                "deepen-2",
                "deepen-3",
                "deepen-4",
                "pivot-1",
                "pivot-2",
                "pivot-3",
                "pivot-4",
                "pivot-5",
                "diagnosis-1",
                "diagnosis-2",
                "diagnosis-3",
                "diagnosis-4",
                "resolution-1",
                "resolution-2",
                "resolution-3",
                "resolution-4",
            ],
            "dryRun": False,
            "pipelineConfig": {
                "drive_folder": "Korsair",
            },
        },
        "error": {
            "Error": "HTTPError",
            "Cause": (
                "{\"errorMessage\": \"503 Server Error: Service Unavailable for url: "
                "https://api.openai.com/v1/videos/video_68ff7134f6f48198abea4904d00048410c673ff7de2887ff/content?variant=video\", "
                "\"errorType\": \"HTTPError\", \"requestId\": \"22b9b74d-9a18-4335-9a0d-5aae3a15653e\", \"stackTrace\": ["
                "\"  File \\\"/var/task/job_worker/handler.py\\\", line 23, in handler\\n"
                "    result = workflow.render_clip(ClipTask(job_context=job_context, clip_id=clip_id))\\n\", "
                "\"  File \\\"/var/task/job_worker/workflow.py\\\", line 95, in render_clip\\n"
                "    clip_result = runner.render_clip(bundle, task.clip_id, context.dry_run)\\n\", "
                "\"  File \\\"/var/task/job_worker/pipeline_runner.py\\\", line 55, in render_clip\\n"
                "    result = orchestrator.render_clip(\\n\", "
                "\"  File \\\"/var/task/src/aivideomaker/orchestrator.py\\\", line 484, in render_clip\\n"
                "    media_assets = self.media_client.submit_prompts([prompt], dry_run=submit_dry_run)\\n\", "
                "\"  File \\\"/var/task/src/aivideomaker/media_pipeline/sora_client.py\\\", line 171, in submit_prompts\\n"
                "    self._download_video(job_id, target)\\n\", "
                "\"  File \\\"/var/task/src/aivideomaker/media_pipeline/sora_client.py\\\", line 328, in _download_video\\n"
                "    response.raise_for_status()\\n\", "
                "\"  File \\\"/var/lang/lib/python3.11/site-packages/requests/models.py\\\", line 1026, in raise_for_status\\n"
                "    raise HTTPError(http_error_msg, response=self)\\n\"]}"
            ),
        },
        "action": "MARK_FAILED",
    }

    worker_handler.handler(event, None)

    assert repo.records[original_job_id]["status"] == "FAILED"
    assert repo.records[original_job_id]["error_message"].startswith("{")


def test_mark_running_transitions_when_expected(tmp_path):
    settings = build_settings(tmp_path)
    runner = StubRunner(tmp_path)
    storage = RecordingStorage(tmp_path / "snapshots")
    store = RecordingBundleStore()
    repo = RecordingRepository()
    repo.records["story"] = {"status": "QUEUED"}
    workflow = PipelineWorkflow(settings=settings, repository=repo, storage=storage, bundle_store=store, runner=runner)

    metadata = JobMetadata(job_id="story", article_url="https://example.com/story")
    raw_payload = {"jobId": metadata.job_id, "articleUrl": metadata.article_url}
    result = workflow.mark_running(metadata, raw_payload)

    assert repo.records["story"]["status"] == "RUNNING"
    assert repo.transitions[-1] == ("story", "QUEUED")
    assert result == {"job": raw_payload}


def test_mark_running_updates_when_transition_fails(tmp_path):
    settings = build_settings(tmp_path)
    runner = StubRunner(tmp_path)
    storage = RecordingStorage(tmp_path / "snapshots")
    store = RecordingBundleStore()
    repo = RecordingRepository()
    repo.records["story"] = {"status": "RUNNING"}
    workflow = PipelineWorkflow(settings=settings, repository=repo, storage=storage, bundle_store=store, runner=runner)

    metadata = JobMetadata(job_id="story", article_url="https://example.com/story")
    raw_payload = {"jobId": metadata.job_id, "articleUrl": metadata.article_url}
    workflow.mark_running(metadata, raw_payload)

    assert repo.updates[-1][1].status == "RUNNING"
