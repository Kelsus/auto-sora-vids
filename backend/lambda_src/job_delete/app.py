from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

import boto3

from common import ArtifactCleanupError, JobsRepository, RepositoryError, delete_job_artifacts
from common.dynamodb_utils import normalize_dynamodb_value

from job_delete.http import bad_request, cors_preflight_response, no_content, not_found, server_error
from job_delete.store import DeleteError, JobDeleteStore, JobDoesNotExist

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class JobDeleteApplication:
    """Handles deletion of job metadata."""

    def __init__(
        self,
        store: JobDeleteStore | None = None,
        repository: JobsRepository | None = None,
        s3_client: Any | None = None,
    ) -> None:
        table_name = os.environ["JOBS_TABLE_NAME"]
        self._table_name = table_name
        self._repository = repository
        if store is not None:
            self._store = store
        else:
            self._store = JobDeleteStore(
                table_name,
                repository=self._ensure_repository(),
            )
        self._s3 = s3_client

    def handle_event(self, event: Dict[str, Any]) -> Dict[str, Any]:
        logger.info("Job delete event received")
        if event.get("httpMethod") == "OPTIONS":
            return cors_preflight_response()

        job_id = self._extract_job_id(event)
        if not job_id:
            return bad_request("jobId path parameter is required")

        try:
            delete_artifacts = self._parse_delete_artifacts(event.get("queryStringParameters"))
        except ValueError as exc:
            return bad_request(str(exc))

        if delete_artifacts:
            try:
                repository = self._ensure_repository()
                job_item = repository.get_job(job_id)
            except RepositoryError:
                logger.exception("Failed to load job metadata for %s", job_id)
                return server_error("Failed to load job")

            if job_item is None:
                return not_found(job_id)

            normalized = normalize_dynamodb_value(job_item)
            s3_client = self._ensure_s3_client()
            try:
                delete_job_artifacts(normalized, s3_client, logger=logger)
            except ArtifactCleanupError:
                logger.exception("Failed to delete artifacts for job %s", job_id)
                return server_error("Failed to delete job artifacts")

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

    @staticmethod
    def _parse_delete_artifacts(query_params: Optional[Dict[str, Any]]) -> bool:
        if not query_params:
            return False
        raw_value = query_params.get("delete_artifacts")
        if raw_value is None:
            return False
        if isinstance(raw_value, bool):
            return raw_value
        if isinstance(raw_value, str):
            normalized = raw_value.strip().lower()
            if normalized in {"true", "1", "yes"}:
                return True
            if normalized in {"false", "0", "no"}:
                return False
        raise ValueError("delete_artifacts must be a boolean query parameter")

    def _ensure_repository(self) -> JobsRepository:
        if self._repository is None:
            self._repository = JobsRepository(self._table_name)
        return self._repository

    def _ensure_s3_client(self):
        if self._s3 is None:
            self._s3 = boto3.client("s3")
        return self._s3


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:  # pragma: no cover - AWS entry
    return JobDeleteApplication().handle_event(event)
