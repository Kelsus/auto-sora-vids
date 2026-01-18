from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

import boto3

from job_update.cancellation import (
    CancellationError,
    InvalidCancellationState,
    JobCancellationService,
)
from job_update.http import (
    bad_request,
    cors_preflight_response,
    conflict,
    not_found,
    ok,
    parse_body,
    server_error,
)
from job_update.models import JobUpdatePayload
from job_update.store import JobDoesNotExist, JobUpdateStore, UpdateError

from common import JobsRepository, RepositoryError, ArtifactCleanupError, delete_job_artifacts
from common.dynamodb_utils import normalize_dynamodb_value, serialize_dynamodb_value
from common.time_utils import utc_now_iso
from job_scheduler.executor import ExecutionLauncher
from job_scheduler.models import ScheduledJob

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class JobUpdateApplication:
    """Handles partial updates to job metadata."""

    def __init__(
        self,
        store: JobUpdateStore | None = None,
        cancellation_service: JobCancellationService | None = None,
        repository: JobsRepository | None = None,
        s3_client: Any | None = None,
    ) -> None:
        table_name = os.environ["JOBS_TABLE_NAME"]
        self._store = store or JobUpdateStore(table_name)
        self._repository = repository or JobsRepository(table_name)
        self._s3 = s3_client or boto3.client("s3")
        state_machine_arn = os.environ.get("STATE_MACHINE_ARN")
        if cancellation_service is not None:
            self._cancellation = cancellation_service
        elif state_machine_arn:
            self._cancellation = JobCancellationService(
                table_name=table_name,
                state_machine_arn=state_machine_arn,
                repository=self._repository,
            )
        else:
            self._cancellation = None
        self._execution_launcher: ExecutionLauncher | None = None
        if state_machine_arn:
            self._execution_launcher = ExecutionLauncher(state_machine_arn=state_machine_arn)

    def handle_event(self, event: Dict[str, Any]) -> Dict[str, Any]:
        logger.info("Job update event received")
        if event.get("httpMethod") == "OPTIONS":
            return cors_preflight_response()

        job_id = self._extract_job_id(event)
        if not job_id:
            return bad_request("jobId path parameter is required")

        try:
            payload_dict = parse_body(event)
        except ValueError as exc:
            return bad_request(str(exc))

        review_action = None
        review_notes = None
        script_override_text = None
        if isinstance(payload_dict, dict):
            review_action = payload_dict.pop("review_action", None)
            review_notes = payload_dict.pop("review_notes", None)
            script_override_text = payload_dict.pop("script_text", None)

        if script_override_text is not None:
            return self._handle_script_override(job_id, str(script_override_text))

        payload: Optional[JobUpdatePayload] = None
        if payload_dict:
            try:
                payload = JobUpdatePayload.from_dict(payload_dict)
            except ValueError as exc:
                return bad_request(str(exc))
        elif not review_action:
            return bad_request("Request must include at least one updatable field or review_action")

        if review_action:
            return self._handle_review_action(job_id, str(review_action), review_notes)

        if payload is None:
            return bad_request("Request must include at least one updatable field")

        if payload.delete_artifacts is not None and payload.status != "PENDING":
            return bad_request("delete_artifacts can only be used when setting status to PENDING")

        existing_job: Dict[str, Any] | None = None
        if payload.status == "PENDING" or payload.delete_artifacts is not None:
            try:
                raw_job = self._repository.get_job(job_id)
            except RepositoryError:
                logger.exception("Failed to load job metadata for %s", job_id)
                return server_error("Failed to load job")
            if raw_job is None:
                return not_found(job_id)
            existing_job = normalize_dynamodb_value(raw_job)

        if payload.status == "CANCELED":
            if payload.is_provided("job_type") or payload.is_provided("pipeline_config") or payload.is_provided("scheduled_datetime"):
                return bad_request("status=CANCELED cannot be combined with other fields")
            if not self._cancellation:
                logger.error("Cancellation requested but service is not configured")
                return server_error("Cancellation not configured")
            try:
                updated = self._cancellation.cancel(job_id)
            except JobDoesNotExist:
                return not_found(job_id)
            except InvalidCancellationState as exc:
                return conflict(str(exc))
            except CancellationError:
                logger.exception("Failed to cancel job")
                return server_error("Failed to cancel job")
            return ok(updated)

        should_delete = False
        if payload.status == "PENDING" and existing_job:
            previous_status = str(existing_job.get("status", "")).upper()
            if previous_status == "COMPLETED":
                should_delete = True
            elif previous_status == "FAILED":
                should_delete = bool(payload.delete_artifacts)
            else:
                if payload.delete_artifacts is not None:
                    return bad_request("delete_artifacts is only valid when transitioning from FAILED to PENDING")

        if should_delete and existing_job:
            try:
                delete_job_artifacts(existing_job, self._s3, logger=logger)
            except ArtifactCleanupError:
                logger.exception("Failed to delete S3 artifacts for job %s", job_id)
                return server_error("Failed to delete job artifacts")

            # Clear resume_from_bundle since we deleted the artifacts it would reference
            try:
                self._repository.update_fields(
                    job_id=job_id,
                    set_parts=["#updated_at = :updated_at"],
                    remove_parts=["#metadata.#pipeline_config.#resume_from_bundle"],
                    attribute_names={
                        "#metadata": "metadata",
                        "#pipeline_config": "pipeline_config",
                        "#resume_from_bundle": "resume_from_bundle",
                        "#updated_at": "updated_at",
                    },
                    attribute_values={":updated_at": utc_now_iso()},
                )
                logger.info("Cleared resume_from_bundle for job %s after artifact deletion", job_id)
            except RepositoryError:
                logger.warning("Failed to clear resume_from_bundle for job %s (may not exist)", job_id)

        try:
            updated = self._store.update_job(job_id, payload)
        except JobDoesNotExist:
            return not_found(job_id)
        except UpdateError:
            logger.exception("Failed to update job")
            return server_error("Failed to update job")

        return ok(updated)

    @staticmethod
    def _extract_job_id(event: Dict[str, Any]) -> str | None:
        path_params = event.get("pathParameters") or {}
        job_id = path_params.get("jobId")
        if job_id and job_id.strip():
            return job_id.strip()
        return None

    def _handle_review_action(
        self,
        job_id: str,
        action: str,
        notes: str | None,
    ) -> Dict[str, Any]:
        try:
            raw_job = self._repository.get_job(job_id)
        except RepositoryError:
            logger.exception("Failed to load job metadata for %s", job_id)
            return server_error("Failed to load job")

        if raw_job is None:
            return not_found(job_id)

        job = normalize_dynamodb_value(raw_job)
        status = str(job.get("status", "")).upper()
        allowed_review_states = {"REVIEW", "REVISION_REQUESTED"}
        normalized_action = action.strip().upper()

        if normalized_action not in {"APPROVE", "REDO", "REJECT"}:
            return bad_request("review_action must be APPROVE, REDO, or REJECT")

        if normalized_action != "REJECT" and status not in allowed_review_states:
            return conflict(f"Job {job_id} is not awaiting review (status={status})")

        metadata = dict(job.get("metadata") or {})
        pipeline_config = dict(metadata.get("pipeline_config") or {})
        review_log = list(metadata.get("review_log") or [])
        review_entry = {
            "action": normalized_action,
            "notes": notes,
            "timestamp": utc_now_iso(),
        }

        next_status = status
        review_metadata = job.get("review_metadata") or {}

        if normalized_action == "APPROVE":
            resume_key = review_metadata.get("bundle_key")
            if not resume_key:
                return bad_request("Job does not contain bundle metadata required for approval")
            pipeline_config["resume_from_bundle"] = resume_key
            pipeline_config["pause_after_prompts"] = False
            pipeline_config.pop("stop_before_sora", None)
            pipeline_config.pop("prepare_voice_during_prompts", None)
            metadata["pipeline_config"] = pipeline_config
            metadata.pop("review_feedback", None)
            next_status = "PENDING"
        elif normalized_action == "REDO":
            redo_context = self._build_review_context(job, review_metadata)
            if redo_context:
                review_entry["context"] = redo_context
            metadata["review_feedback"] = review_entry
            pipeline_config["pause_after_prompts"] = True
            metadata["pipeline_config"] = pipeline_config
            next_status = "PENDING"
        else:  # REJECT
            next_status = "REJECTED"

        metadata["review_log"] = review_log + [review_entry]
        metadata["latest_review"] = review_entry

        sanitized_metadata = serialize_dynamodb_value(metadata)

        attributes = {
            "metadata": sanitized_metadata,
            "current_execution_arn": None,
            "error_message": None,
            "review_metadata": None,
            "stage": None,
        }
        self._repository.update_status(
            job_id,
            next_status,
            attributes,
        )
        updated = self._repository.get_job(job_id)
        if not updated:
            return server_error("Failed to load updated job")
        normalized = normalize_dynamodb_value(updated)

        if normalized_action in {"APPROVE", "REDO"}:
            if self._execution_launcher is None:
                logger.warning("STATE_MACHINE_ARN not configured; job %s will remain PENDING until scheduler runs.", job_id)
            else:
                try:
                    scheduled_job = ScheduledJob.from_item(normalized)
                    execution_arn = self._execution_launcher.start_execution(scheduled_job)
                    self._repository.update_status(
                        job_id,
                        "QUEUED",
                        {
                            "current_execution_arn": execution_arn,
                            "metadata": sanitized_metadata,
                            "review_metadata": None,
                        },
                    )
                    normalized["status"] = "QUEUED"
                    normalized["current_execution_arn"] = execution_arn
                except Exception as exc:  # pragma: no cover
                    logger.exception("Failed to launch Step Functions execution for %s", job_id)
                    return server_error("Failed to launch render execution")

        return ok(normalized)

    def _handle_script_override(self, job_id: str, script_text: str) -> Dict[str, Any]:
        script_text = script_text.strip()
        if not script_text:
            return bad_request("script_text must be a non-empty string")

        try:
            raw_job = self._repository.get_job(job_id)
        except RepositoryError:
            logger.exception("Failed to load job metadata for %s", job_id)
            return server_error("Failed to load job")

        if raw_job is None:
            return not_found(job_id)

        job = normalize_dynamodb_value(raw_job)
        status = str(job.get("status", "")).upper()
        if status not in {"REVIEW", "REVISION_REQUESTED"}:
            return conflict("Script edits are only available while the job is awaiting review")
        bundle_key = job.get("bundle_key") or job.get("metadata", {}).get("bundle_key")
        output_bucket = job.get("output_bucket")
        if not bundle_key or not output_bucket:
            return conflict("Job is missing bundle metadata required for script overrides")

        metadata = dict(job.get("metadata") or {})
        pipeline_config = dict(metadata.get("pipeline_config") or {})
        pipeline_config["resume_from_bundle"] = bundle_key
        pipeline_config["pause_after_prompts"] = False
        metadata["pipeline_config"] = pipeline_config
        metadata.pop("review_feedback", None)
        metadata["script_override"] = {
            "transcript": script_text,
            "updated_at": utc_now_iso(),
        }

        sanitized_metadata = serialize_dynamodb_value(metadata)

        attributes = {
            "metadata": sanitized_metadata,
            "current_execution_arn": None,
            "error_message": None,
            "review_metadata": None,
            "stage": None,
        }

        self._repository.update_status(job_id, "PENDING", attributes)
        updated = self._repository.get_job(job_id)
        if not updated:
            return server_error("Failed to load updated job")
        normalized = normalize_dynamodb_value(updated)

        if self._execution_launcher is None:
            logger.info("STATE_MACHINE_ARN not configured; job %s reset to PENDING after script override", job_id)
            return ok(normalized)

        try:
            scheduled_job = ScheduledJob.from_item(normalized)
            execution_arn = self._execution_launcher.start_execution(scheduled_job)
            self._repository.update_status(
                job_id,
                "QUEUED",
                {
                    "current_execution_arn": execution_arn,
                    "metadata": sanitized_metadata,
                    "review_metadata": None,
                },
            )
            normalized["status"] = "QUEUED"
            normalized["current_execution_arn"] = execution_arn
        except Exception:  # pragma: no cover
            logger.exception("Failed to launch execution after script override for %s", job_id)
            return server_error("Failed to launch render execution")

        return ok(normalized)

    @staticmethod
    def _build_review_context(job: Dict[str, Any], review_metadata: Dict[str, Any]) -> Dict[str, Any]:
        if not review_metadata:
            return {}

        context: Dict[str, Any] = {}
        bundle_key = review_metadata.get("bundle_key")
        if isinstance(bundle_key, str) and bundle_key:
            context["bundle_key"] = bundle_key
        clip_ids = review_metadata.get("clip_ids")
        if isinstance(clip_ids, list) and clip_ids:
            context["clip_ids"] = clip_ids
        output_prefix = review_metadata.get("output_prefix")
        if isinstance(output_prefix, str) and output_prefix:
            context["output_prefix"] = output_prefix
        article = review_metadata.get("article")
        if isinstance(article, dict) and article:
            context["article"] = article
        script_snapshot = review_metadata.get("script")
        if isinstance(script_snapshot, dict) and script_snapshot:
            context["script"] = script_snapshot
        narration = review_metadata.get("narration")
        if isinstance(narration, dict) and narration:
            context["narration"] = narration

        article_url = job.get("url")
        if isinstance(article_url, str) and article_url:
            context.setdefault("article", {})
            context["article"].setdefault("url", article_url)

        return context


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:  # pragma: no cover - AWS entry
    return JobUpdateApplication().handle_event(event)
