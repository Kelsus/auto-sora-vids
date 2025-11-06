from __future__ import annotations

import logging
import os
from typing import Any, Dict

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
from common.dynamodb_utils import normalize_dynamodb_value

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

        try:
            payload = JobUpdatePayload.from_dict(payload_dict)
        except ValueError as exc:
            return bad_request(str(exc))

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


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:  # pragma: no cover - AWS entry
    return JobUpdateApplication().handle_event(event)
