from __future__ import annotations

import json
import logging
import mimetypes
import shutil
import subprocess
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from imageio_ffmpeg import get_ffmpeg_exe

from aivideomaker.captions.ass_builder import write_karaoke_ass
from aivideomaker.orchestrator import PipelineBundle
from aivideomaker.script_engine.reviewer import ScriptReviewDecision
from aivideomaker.media_pipeline.sora_client import SoraClient, SoraJobError

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
        pipeline_overrides = dict(metadata.pipeline_config or {})
        metadata_payload = dict(metadata.metadata or {})
        if "pause_after_prompts" not in pipeline_overrides:
            pipeline_overrides["pause_after_prompts"] = True
        resume_key = pipeline_overrides.get("resume_from_bundle")
        review_feedback_entry = metadata_payload.get("review_feedback") if metadata_payload else None
        review_decision = self._review_decision_from_entry(review_feedback_entry)
        human_feedback_present = review_decision is not None

        bundle_key_override: str | None = None
        if isinstance(resume_key, str) and resume_key.strip() and resume_key.lower() not in {"true", "false"}:
            bundle_key_override = resume_key.strip()

        auto_revision_required = False

        if resume_key:
            target_bundle_key = bundle_key_override or self._settings.bundle_key(metadata.job_id)
            bundle = self._bundle_store.load(target_bundle_key)
            clip_ids = [
                prompt.chunk_id
                for prompt in bundle.prompts.media_prompts
                if getattr(prompt, "render_mode", "") == "sora_clip"
            ]
            if not clip_ids:
                clip_ids = [prompt.chunk_id for prompt in bundle.prompts.media_prompts]
        else:
            runner = self._get_runner(pipeline_overrides)
            prompts_result = runner.run_prompts(
                metadata.article_url,
                dry_run=dry_run_value,
                review_feedback=review_decision,
            )
            bundle = prompts_result.bundle
            clip_ids = prompts_result.clip_ids
            script_review = getattr(bundle, "script_review", None)
            script_greenlit = bool(getattr(bundle, "script_greenlit", True))
            auto_revision_required = bool(script_review and script_review.requires_revision)
            if not script_greenlit:
                auto_revision_required = True
            if human_feedback_present:
                auto_revision_required = False
            if auto_revision_required:
                pipeline_overrides["pause_after_prompts"] = True

        bundle_slug = bundle.article.slug
        job_id = metadata.job_id
        if job_id != bundle_slug:
            logger.warning("Job id %s differs from bundle slug %s; keeping original id", job_id, bundle_slug)

        run_dir = self._sync_run_directory(bundle, job_id, source_preference="slug")
        bundle_key = self._settings.bundle_key(job_id)
        if bundle_key_override:
            bundle_key = bundle_key_override
        output_prefix = self._settings.run_prefix(job_id)

        self._write_bundle(run_dir, bundle)
        self._bundle_store.save(bundle_key, bundle)
        self._storage.upload_directory(run_dir, output_prefix)

        pause_for_review = self._should_pause_for_review(pipeline_overrides)
        review_metadata = None
        if pause_for_review:
            review_metadata = self._build_review_metadata(
                bundle=bundle,
                clip_ids=clip_ids,
                bundle_key=bundle_key,
                output_prefix=output_prefix,
                job_id=job_id,
                bucket=self._settings.output_bucket,
            )

        if review_metadata and getattr(bundle, "script_review", None):
            review_metadata["script_review"] = bundle.script_review.model_dump()

        if human_feedback_present and "review_feedback" in metadata_payload:
            metadata_payload.pop("review_feedback", None)

        attributes = {
            "output_bucket": self._settings.output_bucket,
            "output_prefix": output_prefix,
            "bundle_key": bundle_key,
        }
        attributes["review_metadata"] = (
            self._sanitize_for_dynamo(review_metadata) if review_metadata is not None else None
        )
        if metadata_payload:
            attributes["metadata"] = self._sanitize_for_dynamo(metadata_payload)
        if pause_for_review:
            attributes["current_execution_arn"] = None
        if pause_for_review or auto_revision_required:
            attributes["error_message"] = None
        status = "REVISION_REQUESTED" if auto_revision_required else ("REVIEW" if pause_for_review else "RUNNING")
        self._repository.update_status(job_id, JobStatusUpdate(status=status, attributes=attributes))

        context = JobContext(
            job_id=job_id,
            article_url=metadata.article_url,
            bundle_key=bundle_key,
            output_prefix=output_prefix,
            clip_ids=clip_ids,
            dry_run=dry_run_value,
            pipeline_config=pipeline_overrides,
        )
        logger.info("Prepared prompts for job %s (%d clips)", job_id, len(clip_ids))
        return context

    def render_clip(self, task: ClipTask) -> Dict[str, Any]:
        context = task.job_context
        if context.pipeline_config.get("resume_from_bundle") and task.force_preprocess:
            logger.info(
                "Resume mode: skipping preprocessing task for %s",
                task.clip_id,
            )
            return {"clipId": task.clip_id, "skipped": True, "reason": "resume_preexisting"}
        if context.pipeline_config.get("stop_before_sora") and not task.force_preprocess:
            logger.info(
                "Skipping core clip render for %s (pre-Sora run)",
                task.clip_id,
            )
            return {"clipId": task.clip_id, "skipped": True, "reason": "pre_sora_only"}
        self._refresh_local_run_dir(context.job_id, context.output_prefix)
        bundle = self._bundle_store.load(context.bundle_key)
        runner = self._get_runner(context.pipeline_config)
        clip_result = runner.render_clip(bundle, task.clip_id, context.dry_run)
        return self._store_clip_result(context, clip_result)

    def initiate_clip_render(self, task: ClipTask) -> Dict[str, Any]:
        context = task.job_context
        if context.pipeline_config.get("stop_before_sora") and not task.force_preprocess:
            logger.info(
                "Skipping Sora render for %s (pre-Sora run)",
                task.clip_id,
            )
            return {
                "clipId": task.clip_id,
                "status": "SKIPPED",
                "reason": "pre_sora_only",
            }

        self._refresh_local_run_dir(context.job_id, context.output_prefix)
        bundle = self._bundle_store.load(context.bundle_key)
        self._sync_run_directory(bundle, context.job_id, source_preference="job")
        runner = self._get_runner(context.pipeline_config)
        orchestrator = runner._ensure_orchestrator()
        provider = orchestrator.config.media_provider.lower()

        if provider != "sora":
            logger.info(
                "Media provider %s is not Sora; processing clip %s synchronously",
                provider,
                task.clip_id,
            )
            clip_result = runner.render_clip(bundle, task.clip_id, context.dry_run)
            self._store_clip_result(context, clip_result)
            return {
                "clipId": task.clip_id,
                "status": "COMPLETED",
                "provider": provider,
                "mode": "SYNC",
                "jobId": None,
                "targetPath": None,
            }

        sora_client = self._ensure_sora_client(orchestrator)
        run_dirs = orchestrator._prepare_run_environment(
            bundle.article.slug,
            self._settings.data_root,
            cleanup=False,
        )

        if self._bundle_has_clip(bundle, task.clip_id):
            logger.info(
                "Clip %s already recorded in bundle; skipping Sora submission",
                task.clip_id,
            )
            return {
                "clipId": task.clip_id,
                "status": "SKIPPED",
                "reason": "existing_asset",
                "provider": provider,
            }

        existing_clip = orchestrator._existing_clip_path(run_dirs, task.clip_id)
        if existing_clip:
            logger.info(
                "Found existing clip for %s at %s; skipping new Sora job",
                task.clip_id,
                existing_clip,
            )
            return {
                "clipId": task.clip_id,
                "status": "SKIPPED",
                "reason": "existing_file",
                "provider": provider,
            }

        prompt = next(
            (p for p in bundle.prompts.media_prompts if p.chunk_id == task.clip_id),
            None,
        )
        if prompt is None:
            prompt = orchestrator._fallback_prompt(bundle, task.clip_id)

        job_info = sora_client.initiate_job(prompt, dry_run=context.dry_run)
        target_relative = self._sora_target_relative(run_dirs, task.clip_id)

        status = str(job_info.get("status", "IN_PROGRESS")).upper()
        job_id = job_info.get("job_id")
        logger.info(
            "Started Sora job for clip %s (job_id=%s, status=%s)",
            task.clip_id,
            job_id,
            status,
        )
        return {
            "clipId": task.clip_id,
            "status": status,
            "jobId": job_id,
            "targetPath": target_relative,
            "provider": provider,
        }

    def poll_clip_render(self, task: ClipTask, render_job: Dict[str, Any]) -> Dict[str, Any]:
        status = str(render_job.get("status", "")).upper()
        if status in {"COMPLETED", "SKIPPED"}:
            return render_job

        job_id = render_job.get("jobId")
        if not job_id:
            render_job["status"] = "COMPLETED"
            return render_job

        context = task.job_context
        runner = self._get_runner(context.pipeline_config)
        orchestrator = runner._ensure_orchestrator()
        sora_client = self._ensure_sora_client(orchestrator)

        payload = sora_client.get_job_status(str(job_id))
        job_status = str(payload.get("status", "IN_PROGRESS")).lower()
        if job_status == "completed":
            render_job = dict(render_job)
            render_job["status"] = "COMPLETED"
            render_job["jobDetails"] = payload
            return render_job
        if job_status == "failed":
            error_message = payload.get("error") or payload
            raise SoraJobError(f"Sora job {job_id} failed: {error_message}")

        render_job = dict(render_job)
        render_job["status"] = "IN_PROGRESS"
        render_job["jobDetails"] = payload
        return render_job

    def complete_clip_render(self, task: ClipTask, render_job: Dict[str, Any]) -> Dict[str, Any]:
        status = str(render_job.get("status", "")).upper()
        provider = render_job.get("provider")
        if status == "SKIPPED":
            logger.info(
                "Sora render skipped for clip %s (reason=%s)",
                task.clip_id,
                render_job.get("reason"),
            )
            return {"clipId": task.clip_id, "status": "SKIPPED"}

        if render_job.get("mode") == "SYNC":
            return {"clipId": task.clip_id, "status": "COMPLETED"}

        context = task.job_context
        self._refresh_local_run_dir(context.job_id, context.output_prefix)
        bundle = self._bundle_store.load(context.bundle_key)
        self._sync_run_directory(bundle, context.job_id, source_preference="job")
        runner = self._get_runner(context.pipeline_config)
        orchestrator = runner._ensure_orchestrator()

        if provider != "sora":
            logger.info(
                "Provider %s handled synchronously; nothing to finalize",
                provider,
            )
            return {"clipId": task.clip_id, "status": "COMPLETED"}

        run_dirs = orchestrator._prepare_run_environment(
            bundle.article.slug,
            self._settings.data_root,
            cleanup=False,
        )
        relative_target = render_job.get("targetPath")
        if not relative_target:
            relative_target = self._sora_target_relative(run_dirs, task.clip_id)
        target_path = run_dirs["run_dir"] / Path(relative_target)
        target_path.parent.mkdir(parents=True, exist_ok=True)

        job_id = render_job.get("jobId")
        if job_id:
            sora_client = self._ensure_sora_client(orchestrator)
            sora_client.download_job(str(job_id), target_path)

        clip_result = runner.render_clip(bundle, task.clip_id, context.dry_run)
        self._store_clip_result(context, clip_result)
        return {"clipId": task.clip_id, "status": "COMPLETED"}

    def stitch_final(self, context: JobContext) -> Dict[str, Any]:
        self._refresh_local_run_dir(context.job_id, context.output_prefix)
        bundle = self._bundle_store.load(context.bundle_key)
        runner = self._get_runner(context.pipeline_config)

        if context.pipeline_config.get("stop_before_sora"):
            logger.info("Skipping final stitch for job %s (pre-Sora run)", context.job_id)
            self._repository.update_status(
                context.job_id,
                JobStatusUpdate(
                    status="READY_FOR_SORA",
                    attributes={
                        "output_bucket": self._settings.output_bucket,
                        "output_prefix": context.output_prefix,
                        "stage": "pre_sora_complete",
                    },
                ),
            )
            return {"finalVideoKey": None}

        self._sync_run_directory(bundle, context.job_id, source_preference="job")
        result_bundle = runner.stitch_final(bundle, context.dry_run)

        run_dir = self._sync_run_directory(result_bundle, context.job_id, source_preference="slug")
        self._write_bundle(run_dir, result_bundle)
        self._bundle_store.save(context.bundle_key, result_bundle)

        absolute_final_video = self._resolve_final_video_path(run_dir, result_bundle.final_video)
        drive_folder = self._resolve_drive_folder(context.job_id, context.pipeline_config)
        self._publish_run_outputs(
            job_id=context.job_id,
            run_dir=run_dir,
            prefix=context.output_prefix,
            final_video_path=absolute_final_video,
            drive_folder=drive_folder,
            include_final=False,
        )

        attributes = {
            "output_bucket": self._settings.output_bucket,
            "output_prefix": context.output_prefix,
            "error_message": None,
        }
        self._repository.update_status(context.job_id, JobStatusUpdate(status="RUNNING", attributes=attributes))
        logger.info("Job %s stitched; awaiting caption pass before final delivery", context.job_id)
        return {"finalVideoKey": None}

    def generate_captions(self, context: JobContext) -> Dict[str, Any]:
        self._refresh_local_run_dir(context.job_id, context.output_prefix)
        bundle = self._bundle_store.load(context.bundle_key)
        run_dir = self._local_run_dir(context.job_id)
        absolute_final_video = self._resolve_final_video_path(run_dir, bundle.final_video)
        drive_folder = self._resolve_drive_folder(context.job_id, context.pipeline_config)
        if not bundle.narration_alignment_payload:
            logger.info("Skipping caption generation for job %s; no alignment payload", context.job_id)
            final_video_key = self._publish_run_outputs(
                job_id=context.job_id,
                run_dir=run_dir,
                prefix=context.output_prefix,
                final_video_path=absolute_final_video,
                drive_folder=drive_folder,
                include_final=True,
            )
            attributes = {
                "output_bucket": self._settings.output_bucket,
                "output_prefix": context.output_prefix,
                "captions_ass_key": None,
                "error_message": None,
                "current_execution_arn": None,
            }
            if final_video_key:
                attributes["final_video_key"] = final_video_key
            self._repository.update_status(
                context.job_id,
                JobStatusUpdate(status="COMPLETED", attributes=attributes),
            )
            return {"status": "SKIPPED"}

        export_dir = run_dir / "exports"
        play_res = self._resolve_caption_play_res(context.pipeline_config)
        captions_path = write_karaoke_ass(
            script=bundle.script,
            alignment=bundle.narration_alignment_payload,
            chunks=bundle.chunks,
            export_dir=export_dir,
            play_res=play_res,
        )

        try:
            relative_captions = captions_path.relative_to(run_dir)
        except ValueError:
            relative_captions = captions_path

        updated_bundle = bundle.model_copy(update={"captions_ass_path": relative_captions})
        self._write_bundle(run_dir, updated_bundle)
        self._bundle_store.save(context.bundle_key, updated_bundle)

        if absolute_final_video:
            self._burn_captions_into_video(absolute_final_video, captions_path, export_dir)

        final_video_key = self._publish_run_outputs(
            job_id=context.job_id,
            run_dir=run_dir,
            prefix=context.output_prefix,
            final_video_path=absolute_final_video,
            drive_folder=drive_folder,
            include_final=True,
        )

        try:
            captions_relative_export = captions_path.relative_to(export_dir).as_posix()
        except ValueError:
            try:
                captions_relative_export = captions_path.relative_to(run_dir).as_posix()
            except ValueError:
                captions_relative_export = captions_path.name

        final_captions_key = self._settings.final_artifact_key(context.job_id, captions_relative_export)
        logger.info("Generated captions for job %s at %s", context.job_id, captions_path)

        attributes = {
            "output_bucket": self._settings.output_bucket,
            "output_prefix": context.output_prefix,
            "captions_ass_key": final_captions_key,
            "error_message": None,
        }
        if final_video_key:
            attributes["final_video_key"] = final_video_key
        attributes["current_execution_arn"] = None
        self._repository.update_status(
            context.job_id,
            JobStatusUpdate(status="COMPLETED", attributes=attributes),
        )

        return {
            "captionsAssPath": str(relative_captions),
            "captionsFinalKey": final_captions_key,
        }

    def mark_running(self, metadata: JobMetadata, raw_payload: Dict[str, Any]) -> Dict[str, Any]:
        transitioned = self._repository.transition_to_running(metadata.job_id)
        if not transitioned:
            self._repository.update_status(metadata.job_id, JobStatusUpdate(status="RUNNING", attributes={}))
        logger.info("Job %s marked as RUNNING", metadata.job_id)
        return {"job": raw_payload}

    def mark_failed(
        self,
        context: JobContext | None,
        *,
        metadata: JobMetadata | None = None,
        error: Dict[str, Any] | None = None,
    ) -> None:
        message = "Unknown error"
        if error:
            if isinstance(error, dict):
                message = json.dumps(error)[:400]
            else:
                message = str(error)[:400]
        job_id: str | None = None
        if context:
            job_id = context.job_id
        elif metadata:
            job_id = metadata.job_id
        if not job_id:
            raise ValueError("Mark failed requires job context or metadata with job id")

        update = JobStatusUpdate(
            status="FAILED",
            attributes={
                "error_message": message,
                "current_execution_arn": None,
            },
        )
        self._repository.update_status(job_id, update)
        logger.error("Job %s failed: %s", job_id, message)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _should_pause_for_review(pipeline_config: Dict[str, Any]) -> bool:
        value = pipeline_config.get("pause_after_prompts")
        if isinstance(value, str):
            normalized = value.strip().lower()
            return normalized in {"1", "true", "yes", "on"}
        return bool(value)

    @staticmethod
    def _review_decision_from_entry(entry: Dict[str, Any] | None) -> ScriptReviewDecision | None:
        if not entry or not isinstance(entry, dict):
            return None

        action = str(entry.get("action") or "REVISION_REQUESTED").upper()
        context = entry.get("context") if isinstance(entry.get("context"), dict) else {}

        raw_notes = entry.get("notes")
        notes = str(raw_notes or context.get("notes") or "").strip()
        summary = context.get("summary") or notes or f"Human review action: {action}"

        strengths = list(context.get("strengths") or [])
        if action == "APPROVE" and not strengths:
            strengths = ["Human reviewer approved previous script."]

        concerns = list(context.get("concerns") or [])
        if not concerns and notes:
            concerns = [notes]

        action_items = list(context.get("action_items") or [])
        if not action_items and concerns:
            action_items = list(concerns)

        return ScriptReviewDecision(
            verdict="revise" if action != "APPROVE" else "approve",
            summary=summary,
            strengths=strengths,
            concerns=concerns,
            action_items=action_items,
        )

    def _build_review_metadata(
        self,
        bundle: PipelineBundle,
        clip_ids: list[str],
        bundle_key: str,
        output_prefix: str,
        job_id: str,
        bucket: str,
    ) -> dict[str, Any]:
        article_doc = getattr(bundle.article, "article", None)
        article_meta = getattr(article_doc, "metadata", None) if article_doc else None
        article_info: dict[str, Any]
        if article_meta is None:
            article_info = {
                "title": getattr(bundle.article, "title", getattr(bundle.article, "slug", None)),
                "url": None,
                "source": getattr(bundle.article, "source", None),
            }
        else:
            article_info = {
                "title": article_meta.title,
                "url": str(article_meta.url),
                "source": article_meta.source,
            }
        script = bundle.script
        beats: list[dict[str, Any]] = []
        beat_iterable = getattr(script, "beats", []) or []
        for idx, beat in enumerate(beat_iterable, start=1):
            visual_type = beat.visual.type if beat.visual and beat.visual.type else None
            visual_macro = beat.visual.macro if beat.visual else None
            beats.append(
                {
                    "index": idx,
                    "id": beat.id,
                    "purpose": beat.purpose,
                    "visual_type": visual_type,
                    "visual_macro": visual_macro,
                    "intent": beat.intent,
                    "suspense_level": beat.suspense_level,
                    "estimated_duration_sec": beat.estimated_duration_sec,
                    "audio_mood": beat.audio_mood,
                    "visual_seed": beat.visual_seed,
                }
            )

        narration = {
            "transcript_path": self._asset_s3_uri(
                bundle.voice_transcript,
                output_prefix,
                job_id,
                bundle.article.slug,
                bucket,
            ),
            "audio_path": self._asset_s3_uri(
                bundle.narration_audio,
                output_prefix,
                job_id,
                bundle.article.slug,
                bucket,
            ),
            "alignment_path": self._asset_s3_uri(
                bundle.narration_alignment,
                output_prefix,
                job_id,
                bundle.article.slug,
                bucket,
            ),
            "has_alignment_payload": bool(bundle.narration_alignment_payload),
        }

        return {
            "bundle_key": bundle_key,
            "output_prefix": output_prefix,
            "clip_ids": clip_ids,
            "article": article_info,
            "script": {
                "premise": getattr(script, "premise", None),
                "controversy_summary": getattr(script, "controversy_summary", None),
                "withheld_context": getattr(script, "withheld_context", None),
                "final_reveal": getattr(script, "final_reveal", None),
                "target_runtime_sec": getattr(script, "target_runtime_sec", None),
                "target_beat_count": getattr(script, "target_beat_count", None),
                "narrative_style": getattr(script, "narrative_style", None),
                "beats": beats,
            },
            "narration": narration,
        }

    def _asset_s3_uri(
        self,
        asset_path: Path | str | None,
        output_prefix: str,
        job_id: str,
        slug: str,
        bucket: str,
    ) -> str | None:
        if not asset_path:
            return None
        path = Path(asset_path)
        relative = self._relative_asset_path(path, job_id, slug)
        if not relative:
            return path.as_posix()
        key = "/".join(part.strip("/") for part in [output_prefix, relative] if part)
        return f"s3://{bucket}/{key}"

    def _relative_asset_path(self, path: Path, job_id: str, slug: str) -> str | None:
        candidates = [
            self._local_run_dir(job_id),
            self._settings.data_root / slug,
            self._settings.data_root,
        ]
        for base in candidates:
            try:
                relative = path.relative_to(base)
                return relative.as_posix().lstrip("/")
            except ValueError:
                continue
        return None

    def _sanitize_for_dynamo(self, value: Any):
        if isinstance(value, float):
            return Decimal(str(value))
        if isinstance(value, list):
            return [self._sanitize_for_dynamo(v) for v in value]
        if isinstance(value, dict):
            return {k: self._sanitize_for_dynamo(v) for k, v in value.items()}
        return value

    def _store_clip_result(self, context: JobContext, clip_result: Any) -> Dict[str, Any]:
        updated_bundle = clip_result.bundle
        clip_path = clip_result.clip_asset
        source_run_dir = self._settings.data_root / updated_bundle.article.slug
        try:
            relative_clip = clip_path.relative_to(source_run_dir)
        except ValueError:
            relative_clip = clip_path
        run_dir = self._sync_run_directory(updated_bundle, context.job_id, source_preference="slug")
        self._write_bundle(run_dir, updated_bundle)
        self._bundle_store.save(context.bundle_key, updated_bundle)
        self._storage.upload_directory(run_dir, context.output_prefix)

        clip_identifier = getattr(clip_result, "clip_id", Path(clip_path).stem)
        if isinstance(relative_clip, Path):
            log_clip = relative_clip.as_posix()
        else:
            log_clip = str(relative_clip)

        logger.info(
            "Uploaded clip %s for job %s at %s",
            clip_identifier,
            context.job_id,
            log_clip,
        )
        return {"clipId": clip_identifier}

    def _bundle_has_clip(self, bundle: PipelineBundle, clip_id: str) -> bool:
        for asset in bundle.sora_assets:
            if Path(asset).stem == clip_id:
                return True
        return False

    def _ensure_sora_client(self, orchestrator: Any) -> SoraClient:
        client = getattr(orchestrator, "media_client", None)
        if isinstance(client, SoraClient):
            return client
        raise RuntimeError("Sora media client is not configured")

    def _sora_target_relative(self, run_dirs: Dict[str, Path], clip_id: str) -> str:
        return (run_dirs["sora_dir"].relative_to(run_dirs["run_dir"]) / f"{clip_id}.mp4").as_posix()

    def _resolve_dry_run(self, override: bool | None) -> bool:
        return override if override is not None else self._settings.default_dry_run

    def _local_run_dir(self, job_id: str) -> Path:
        return self._settings.data_root / job_id

    def _resolve_caption_play_res(self, overrides: Optional[Mapping[str, Any]]) -> tuple[int, int]:
        candidate: Optional[str] = None
        if overrides and isinstance(overrides, Mapping):
            size_override = overrides.get("sora_size")
            if isinstance(size_override, str):
                candidate = size_override
        if candidate:
            parsed = self._parse_resolution(candidate)
            if parsed:
                return parsed
        try:
            runner = self._get_runner(overrides or {})
            orchestrator = runner._ensure_orchestrator()  # type: ignore[attr-defined]
            size_value = getattr(orchestrator.config, "sora_size", None)
            if isinstance(size_value, str):
                parsed = self._parse_resolution(size_value)
                if parsed:
                    return parsed
        except Exception:  # pragma: no cover - defensive guard
            pass
        return (720, 1280)

    @staticmethod
    def _parse_resolution(value: str) -> Optional[tuple[int, int]]:
        try:
            width_str, height_str = value.lower().split("x", 1)
            return int(width_str), int(height_str)
        except Exception:
            return None

    def _write_bundle(self, run_dir: Path, bundle: PipelineBundle) -> None:
        run_dir.mkdir(parents=True, exist_ok=True)
        bundle_path = run_dir / "bundle.json"
        bundle_path.write_text(json.dumps(bundle.model_dump(mode="json"), indent=2), encoding="utf-8")

    def _sync_run_directory(self, bundle: PipelineBundle, job_id: str, source_preference: str = "slug") -> Path:
        target_dir = self._local_run_dir(job_id)
        slug = bundle.article.slug
        source_dir = self._settings.data_root / slug
        try:
            target_resolved = target_dir.resolve()
        except FileNotFoundError:
            target_resolved = target_dir
        try:
            source_resolved = source_dir.resolve()
        except FileNotFoundError:
            source_resolved = source_dir

        if source_resolved == target_resolved:
            target_dir.mkdir(parents=True, exist_ok=True)
            return target_dir

        preferred = source_preference.lower()
        if preferred not in {"slug", "job"}:
            raise ValueError(f"Invalid source_preference '{source_preference}'")

        primary_dir = source_dir if preferred == "slug" else target_dir
        mirror_dir = target_dir if preferred == "slug" else source_dir

        if primary_dir.exists() and any(primary_dir.iterdir()):
            if mirror_dir.exists():
                shutil.rmtree(mirror_dir)
            shutil.copytree(primary_dir, mirror_dir)
        else:
            mirror_dir.mkdir(parents=True, exist_ok=True)
        return target_dir

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

    def _resolve_final_video_path(self, run_dir: Path, final_video: Optional[Path | str]) -> Optional[Path]:
        if not final_video:
            return None
        candidate = Path(final_video)
        if not candidate.is_absolute():
            candidate = run_dir / candidate
        if not candidate.exists():
            return None
        return candidate

    def _publish_run_outputs(
        self,
        *,
        job_id: str,
        run_dir: Path,
        prefix: str,
        final_video_path: Optional[Path],
        drive_folder: Optional[str],
        include_final: bool,
    ) -> Optional[str]:
        self._storage.upload_directory(run_dir, prefix)
        if not include_final:
            return None
        return self._copy_exports_to_final(
            job_id=job_id,
            run_dir=run_dir,
            final_video_path=final_video_path,
            drive_folder=drive_folder,
        )

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

    def _burn_captions_into_video(self, video_path: Path, captions_path: Path, export_dir: Path) -> None:
        temp_output = video_path.with_suffix(".captions.mp4")
        fonts_dir = export_dir / "fonts"
        filter_expr = f"subtitles={captions_path}"
        if fonts_dir.exists() and fonts_dir.is_dir():
            filter_expr = f"{filter_expr}:fontsdir={fonts_dir}"

        ffmpeg_binary = get_ffmpeg_exe()
        base_cmd = [
            ffmpeg_binary,
            "-y",
            "-i",
            str(video_path),
            "-vf",
            filter_expr,
            "-c:v",
            "libx264",
            "-crf",
            "18",
            "-preset",
            "slow",
            "-movflags",
            "+faststart",
        ]
        cmd_with_audio = base_cmd + [
            "-map",
            "0:v:0",
            "-map",
            "0:a?",
            "-c:a",
            "copy",
            str(temp_output),
        ]
        cmd_video_only = base_cmd + [
            str(temp_output),
        ]

        try:
            subprocess.run(
                cmd_with_audio,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except subprocess.CalledProcessError as exc:
            logger.warning(
                "Failed to preserve original audio while burning captions; retrying without audio. ffmpeg stderr: %s",
                exc.stderr.decode("utf-8", errors="ignore"),
            )
            subprocess.run(
                cmd_video_only,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

        video_path.unlink()
        temp_output.rename(video_path)

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

    def _should_upload_final_artifacts(self, pipeline_config: Optional[Mapping[str, Any]]) -> bool:
        if not pipeline_config or not isinstance(pipeline_config, Mapping):
            return True

        overrides = dict(pipeline_config)

        if "deliver_final_exports" in overrides:
            return self._coerce_bool(overrides["deliver_final_exports"], default=True)

        if "deliverFinalExports" in overrides:
            return self._coerce_bool(overrides["deliverFinalExports"], default=True)

        if "disable_final_exports" in overrides:
            return not self._coerce_bool(overrides["disable_final_exports"], default=False)

        if "disableFinalExports" in overrides:
            return not self._coerce_bool(overrides["disableFinalExports"], default=False)

        if "skip_drive_upload" in overrides:
            return not self._coerce_bool(overrides["skip_drive_upload"], default=False)

        if "skipDriveUpload" in overrides:
            return not self._coerce_bool(overrides["skipDriveUpload"], default=False)

        return True

    @staticmethod
    def _coerce_bool(value: Any, *, default: bool) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return value != 0
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"true", "1", "yes", "on"}:
                return True
            if normalized in {"false", "0", "no", "off"}:
                return False
        return default
