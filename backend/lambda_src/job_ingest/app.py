from __future__ import annotations

import logging
import os
from typing import Any, Dict

from common.time_utils import utc_now
from job_ingest.http import (
    HttpRequestParser,
    bad_request,
    cors_preflight_response,
    created,
    server_error,
)
from job_ingest.models import JobRequest, ValidationError
from job_ingest.repository import JobRecord, JobStore, PersistenceError

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class JobIngestApplication:
    """Coordinates request parsing, validation, and persistence."""

    def __init__(
        self,
        repository: JobStore | None = None,
        request_parser: HttpRequestParser | None = None,
    ) -> None:
        table_name = os.environ["JOBS_TABLE_NAME"]
        self._repo = repository or JobStore(table_name)
        self._parser = request_parser or HttpRequestParser()

    def handle_event(self, event: Dict[str, Any]) -> Dict[str, Any]:
        logger.info("Job ingest event received")
        if event.get("httpMethod") == "OPTIONS":
            return cors_preflight_response()

        try:
            payload = self._parser.parse(event)
            job_request = JobRequest.from_payload(payload)
        except ValidationError as exc:
            return bad_request(str(exc))

        record = self._build_record(job_request)
        try:
            self._repo.save(record)
        except PersistenceError:
            logger.exception("Failed to persist job")
            return server_error("Failed to create job")

        return created(record.job_id)

    @staticmethod
    def _build_record(job: JobRequest) -> JobRecord:
        job_id = job.job_id
        now = utc_now()
        return JobRecord(
            job_id=job_id,
            url=job.url,
            social_media=job.social_media,
            scheduled_datetime=job.scheduled_datetime,
            job_type=job.job_type,
            status=job.status,
            metadata=dict(job.metadata),
            created_at=now,
            updated_at=now,
        )

def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:  # pragma: no cover - AWS entry
    return JobIngestApplication().handle_event(event)
