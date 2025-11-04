from __future__ import annotations

import logging
import os
from typing import Any, Dict

from job_delete.http import bad_request, cors_preflight_response, no_content, not_found, server_error
from job_delete.store import DeleteError, JobDeleteStore, JobDoesNotExist

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class JobDeleteApplication:
    """Handles deletion of job metadata."""

    def __init__(self, store: JobDeleteStore | None = None) -> None:
        table_name = os.environ["JOBS_TABLE_NAME"]
        self._store = store or JobDeleteStore(table_name)

    def handle_event(self, event: Dict[str, Any]) -> Dict[str, Any]:
        logger.info("Job delete event received")
        if event.get("httpMethod") == "OPTIONS":
            return cors_preflight_response()

        job_id = self._extract_job_id(event)
        if not job_id:
            return bad_request("jobId path parameter is required")

        try:
            self._store.delete_job(job_id)
        except JobDoesNotExist:
            return not_found(job_id)
        except DeleteError:
            logger.exception("Failed to delete job")
            return server_error("Failed to delete job")

        return no_content()

    @staticmethod
    def _extract_job_id(event: Dict[str, Any]) -> str | None:
        path_params = event.get("pathParameters") or {}
        job_id = path_params.get("jobId")
        if job_id and job_id.strip():
            return job_id.strip()
        return None


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:  # pragma: no cover - AWS entry
    return JobDeleteApplication().handle_event(event)
