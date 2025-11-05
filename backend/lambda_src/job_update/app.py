from __future__ import annotations

import logging
import os
from typing import Any, Dict

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

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class JobUpdateApplication:
    """Handles partial updates to job metadata."""

    def __init__(
        self,
        store: JobUpdateStore | None = None,
        cancellation_service: JobCancellationService | None = None,
    ) -> None:
        table_name = os.environ["JOBS_TABLE_NAME"]
        self._store = store or JobUpdateStore(table_name)
        state_machine_arn = os.environ.get("STATE_MACHINE_ARN")
        if cancellation_service is not None:
            self._cancellation = cancellation_service
        elif state_machine_arn:
            self._cancellation = JobCancellationService(
                table_name=table_name,
                state_machine_arn=state_machine_arn,
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
