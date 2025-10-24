from __future__ import annotations

import json
import logging
import mimetypes
import shutil
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

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
        self._injected_runner = runner
        self._runner_cache: dict[str, PipelineRunner] = {}

    # ------------------------------------------------------------------
    # State machine actions
    # ------------------------------------------------------------------

    def generate_prompts(self, metadata: JobMetadata, dry_run: bool | None = None) -> JobContext:
        dry_run_value = self._resolve_dry_run(dry_run)
        runner = self._get_runner(metadata.pipeline_config)
        prompts_result = runner.run_prompts(metadata.article_url, dry_run=dry_run_value)
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
            pipeline_config=metadata.pipeline_config,
        )
        logger.info("Prepared prompts for job %s (%d clips)", job_id, len(clip_ids))
        return context

    def render_clip(self, task: ClipTask) -> Dict[str, Any]:
        context = task.job_context
        self._refresh_local_run_dir(context.job_id, context.output_prefix)
        bundle = self._bundle_store.load(context.bundle_key)
        run_dir = self._local_run_dir(context.job_id)
        runner = self._get_runner(context.pipeline_config)
        clip_result = runner.render_clip(bundle, task.clip_id, context.dry_run)
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
        runner = self._get_runner(context.pipeline_config)
        result_bundle = runner.stitch_final(bundle, context.dry_run)

        run_dir = self._local_run_dir(context.job_id)
        self._write_bundle(run_dir, result_bundle)
        self._bundle_store.save(context.bundle_key, result_bundle)
        self._storage.upload_directory(run_dir, context.output_prefix)

        final_video_path = result_bundle.final_video
        absolute_final_video = None
        if final_video_path:
            absolute_final_video = Path(final_video_path)
            if not absolute_final_video.is_absolute():
                absolute_final_video = run_dir / absolute_final_video
            if not absolute_final_video.exists():
                absolute_final_video = None

        drive_folder = self._resolve_drive_folder(context.job_id, context.pipeline_config)
        final_video_key = self._copy_exports_to_final(
            context.job_id,
            run_dir,
            absolute_final_video,
            drive_folder,
        )

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

    def _get_runner(self, overrides: Optional[Dict[str, Any]]) -> PipelineRunner:
        if self._injected_runner is not None:
            return self._injected_runner
        payload = overrides or {}
        signature = json.dumps(payload, sort_keys=True)
        runner = self._runner_cache.get(signature)
        if runner is None:
            runner = PipelineRunner(
                data_root=self._settings.data_root,
                base_config_path=self._settings.pipeline_config_path,
                config_overrides=payload,
                veo_credentials_parameter=self._settings.veo_credentials_parameter,
                anthropic_api_key_parameter=self._settings.anthropic_api_key_parameter,
                openai_api_key_parameter=self._settings.openai_api_key_parameter,
                elevenlabs_api_key_parameter=self._settings.elevenlabs_api_key_parameter,
                google_api_key_parameter=self._settings.google_api_key_parameter,
            )
            self._runner_cache[signature] = runner
        return runner

    def _copy_exports_to_final(
        self,
        job_id: str,
        run_dir: Path,
        final_video_path: Optional[Path],
        drive_folder: Optional[str],
    ) -> Optional[str]:
        exports_dir = run_dir / "exports"
        if not exports_dir.exists():
            if final_video_path:
                metadata = {"job-id": job_id}
                if drive_folder:
                    metadata["drive-folder"] = drive_folder
                content_type, _ = mimetypes.guess_type(final_video_path.name)
                key = self._settings.final_video_key(job_id, final_video_path.name)
                self._storage.upload_file(final_video_path, key, metadata=metadata, content_type=content_type)
                return key
            return None

        final_video_key: Optional[str] = None
        metadata = {"job-id": job_id}
        if drive_folder:
            metadata["drive-folder"] = drive_folder
        resolved_final_video = final_video_path.resolve() if final_video_path else None

        for path in exports_dir.rglob("*"):
            if not path.is_file():
                continue
            relative_name = path.relative_to(exports_dir).as_posix()
            destination_key = self._settings.final_artifact_key(job_id, relative_name)
            content_type, _ = mimetypes.guess_type(path.name)
            self._storage.upload_file(path, destination_key, metadata=metadata, content_type=content_type)
            if resolved_final_video and path.resolve() == resolved_final_video:
                final_video_key = destination_key

        if not final_video_key and final_video_path and final_video_path.exists():
            relative_name = final_video_path.name
            destination_key = self._settings.final_artifact_key(job_id, relative_name)
            content_type, _ = mimetypes.guess_type(final_video_path.name)
            self._storage.upload_file(
                final_video_path,
                destination_key,
                metadata=metadata,
                content_type=content_type,
            )
            final_video_key = destination_key

        return final_video_key

    def _resolve_drive_folder(self, job_id: str, pipeline_config: Optional[Mapping[str, Any]]) -> Optional[str]:
        if pipeline_config and isinstance(pipeline_config, Mapping):
            drive_folder = pipeline_config.get("drive_folder")
            if isinstance(drive_folder, str) and drive_folder.strip():
                return drive_folder.strip()
        record = self._repository.fetch(job_id)
        if not record:
            return None
        metadata = record.get("metadata")
        if isinstance(metadata, Mapping):
            pipeline_overrides = metadata.get("pipeline_config")
            if isinstance(pipeline_overrides, Mapping):
                drive_folder = pipeline_overrides.get("drive_folder")
                if isinstance(drive_folder, str) and drive_folder.strip():
                    return drive_folder.strip()
        return None
