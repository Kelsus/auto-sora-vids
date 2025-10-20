from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path
from typing import Any, Dict

from aivideomaker.orchestrator import PipelineBundle

from job_worker.bundle_store import BundleStore
from job_worker.config import WorkerSettings
from job_worker.models import ClipTask, JobContext, JobMetadata, JobStatusUpdate
from job_worker.pipeline_runner import PipelineRunner
from job_worker.repository import JobRepository
from job_worker.storage import ArtifactStorage

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class PipelineWorkflow:
    def __init__(
        self,
        settings: WorkerSettings | None = None,
        repository: JobRepository | None = None,
        storage: ArtifactStorage | None = None,
        bundle_store: BundleStore | None = None,
        runner: PipelineRunner | None = None,
    ) -> None:
        self._settings = settings or WorkerSettings.from_env()
        self._repository = repository or JobRepository(self._settings.jobs_table_name)
        self._storage = storage or ArtifactStorage(self._settings.output_bucket)
        self._bundle_store = bundle_store or BundleStore(self._settings.output_bucket)
        self._runner = runner or PipelineRunner(
            data_root=self._settings.data_root,
            config_path=self._settings.pipeline_config_path,
        )

    # ------------------------------------------------------------------
    # State machine actions
    # ------------------------------------------------------------------

    def generate_prompts(self, metadata: JobMetadata, dry_run: bool | None = None) -> JobContext:
        dry_run_value = self._resolve_dry_run(dry_run)
        prompts_result = self._runner.run_prompts(metadata.article_url, dry_run=dry_run_value)
        bundle = prompts_result.bundle
        job_id = metadata.job_id
        if job_id != bundle.article.slug:
            logger.warning("Job id %s differs from bundle slug %s; using bundle slug", job_id, bundle.article.slug)
            job_id = bundle.article.slug

        run_dir = self._local_run_dir(job_id)
        bundle_key = self._settings.bundle_key(job_id)
        output_prefix = self._settings.run_prefix(job_id)

        self._write_bundle(run_dir, bundle)
        self._bundle_store.save(bundle_key, bundle)
        self._storage.upload_directory(run_dir, output_prefix)

        clip_ids = prompts_result.clip_ids

        update = JobStatusUpdate(
            status="RUNNING",
            attributes={
                "output_bucket": self._settings.output_bucket,
                "output_prefix": output_prefix,
                "bundle_key": bundle_key,
            },
        )
        self._repository.update_status(job_id, update)

        context = JobContext(
            job_id=job_id,
            article_url=metadata.article_url,
            bundle_key=bundle_key,
            output_prefix=output_prefix,
            clip_ids=clip_ids,
            dry_run=dry_run_value,
            social_media=metadata.social_media,
        )
        logger.info("Prepared prompts for job %s (%d clips)", job_id, len(clip_ids))
        return context

    def render_clip(self, task: ClipTask) -> Dict[str, Any]:
        context = task.job_context
        self._refresh_local_run_dir(context.job_id, context.output_prefix)
        bundle = self._bundle_store.load(context.bundle_key)
        run_dir = self._local_run_dir(context.job_id)
        clip_result = self._runner.render_clip(bundle, task.clip_id, context.dry_run)
        updated_bundle = clip_result.bundle
        clip_path = clip_result.clip_asset
        self._write_bundle(run_dir, updated_bundle)
        self._bundle_store.save(context.bundle_key, updated_bundle)
        self._storage.upload_directory(run_dir, context.output_prefix)

        try:
            relative_clip = clip_path.relative_to(run_dir)
        except ValueError:
            relative_clip = clip_path

        logger.info(
            "Uploaded clip %s for job %s at %s",
            task.clip_id,
            context.job_id,
            relative_clip,
        )
        return {"clipId": task.clip_id}

    def stitch_final(self, context: JobContext) -> Dict[str, Any]:
        self._refresh_local_run_dir(context.job_id, context.output_prefix)
        bundle = self._bundle_store.load(context.bundle_key)
        result_bundle = self._runner.stitch_final(bundle, context.dry_run)

        run_dir = self._local_run_dir(context.job_id)
        self._write_bundle(run_dir, result_bundle)
        self._bundle_store.save(context.bundle_key, result_bundle)
        self._storage.upload_directory(run_dir, context.output_prefix)

        final_video_path = result_bundle.final_video
        final_video_key = None
        if final_video_path:
            final_video_path = Path(final_video_path)
            if not final_video_path.is_absolute():
                final_video_path = run_dir / final_video_path
            if final_video_path.exists():
                final_video_key = self._settings.final_video_key(context.job_id, final_video_path.name)
                self._storage.upload_file(final_video_path, final_video_key)

        attributes = {
            "output_bucket": self._settings.output_bucket,
            "output_prefix": context.output_prefix,
        }
        if final_video_key:
            attributes["final_video_key"] = final_video_key

        self._repository.update_status(context.job_id, JobStatusUpdate(status="COMPLETED", attributes=attributes))
        logger.info("Job %s completed", context.job_id)
        return {"finalVideoKey": final_video_key}

    def mark_failed(self, context: JobContext, error: Dict[str, Any] | None = None) -> None:
        message = "Unknown error"
        if error:
            if isinstance(error, dict):
                message = json.dumps(error)[:400]
            else:
                message = str(error)[:400]
        update = JobStatusUpdate(status="FAILED", attributes={"error_message": message})
        self._repository.update_status(context.job_id, update)
        logger.error("Job %s failed: %s", context.job_id, message)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _resolve_dry_run(self, override: bool | None) -> bool:
        return override if override is not None else self._settings.default_dry_run

    def _local_run_dir(self, job_id: str) -> Path:
        return self._settings.data_root / job_id

    def _write_bundle(self, run_dir: Path, bundle: PipelineBundle) -> None:
        run_dir.mkdir(parents=True, exist_ok=True)
        bundle_path = run_dir / "bundle.json"
        bundle_path.write_text(json.dumps(bundle.model_dump(mode="json"), indent=2), encoding="utf-8")

    def _refresh_local_run_dir(self, job_id: str, prefix: str) -> None:
        run_dir = self._local_run_dir(job_id)
        if run_dir.exists():
            shutil.rmtree(run_dir)
        run_dir.mkdir(parents=True, exist_ok=True)
        self._storage.download_prefix(prefix, run_dir)
