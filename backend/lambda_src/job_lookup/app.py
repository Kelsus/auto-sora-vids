from __future__ import annotations

import logging
import os
from typing import Any, Dict

from job_lookup.http import bad_request, cors_preflight_response, not_found, ok, server_error
from job_lookup.repository import JobLookupStore, LookupError

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class JobLookupApplication:
    """Handles retrieval of job metadata by ID."""

    def __init__(self, repository: JobLookupStore | None = None) -> None:
        table_name = os.environ["JOBS_TABLE_NAME"]
        self._repository = repository or JobLookupStore(table_name)

    def handle_event(self, event: Dict[str, Any]) -> Dict[str, Any]:
        logger.info("Job lookup event received")
        if event.get("httpMethod") == "OPTIONS":
            return cors_preflight_response()

        job_id = self._extract_job_id(event)
        if not job_id:
            return bad_request("jobId path parameter is required")

        try:
            item = self._repository.get(job_id)
        except LookupError:
            logger.exception("Failed to retrieve job")
            return server_error("Failed to retrieve job")

        if item is None:
            return not_found(job_id)

        return ok(item)

    @staticmethod
    def _extract_job_id(event: Dict[str, Any]) -> str | None:
        path_params = event.get("pathParameters") or {}
        job_id = path_params.get("jobId")
        if job_id and job_id.strip():
            return job_id.strip()
        return None


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:  # pragma: no cover - AWS entry
    return JobLookupApplication().handle_event(event)
