from __future__ import annotations

import logging
import os
from typing import Any, Dict

from common.signed_urls import JobItemSigner
from job_list.http import bad_request, cors_preflight_response, ok, server_error
from job_list.repository import InvalidCursor, JobListStore, ListError

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


DEFAULT_LIMIT = 20
MAX_LIMIT = 100


class JobListApplication:
    """Handles paginated retrieval of job records."""

    def __init__(self, store: JobListStore | None = None, s3_client: Any | None = None) -> None:
        table_name = os.environ["JOBS_TABLE_NAME"]
        self._store = store or JobListStore(table_name)
        self._signer = JobItemSigner(s3_client)

    def handle_event(self, event: Dict[str, Any]) -> Dict[str, Any]:
        logger.info("Job list event received")
        if event.get("httpMethod") == "OPTIONS":
            return cors_preflight_response()

        params = event.get("queryStringParameters") or {}
        try:
            limit = self._parse_limit(params.get("limit"))
        except ValueError as exc:
            return bad_request(str(exc))

        cursor = params.get("cursor")

        status_filter = None
        if params.get("status"):
            status_filter = params["status"].strip().upper()

        try:
            result = self._store.list_jobs(limit=limit, cursor=cursor, status=status_filter)
        except InvalidCursor as exc:
            return bad_request(str(exc))
        except ListError:
            logger.exception("Failed to list jobs")
            return server_error("Failed to list jobs")

        items = result.items
        for item in items:
            self._signer.attach_signed_urls(item)

        body: Dict[str, Any] = {"items": items}
        if result.next_cursor:
            body["nextCursor"] = result.next_cursor
        return ok(body)

    @staticmethod
    def _parse_limit(raw_limit: str | None) -> int:
        if raw_limit is None or not raw_limit.strip():
            return DEFAULT_LIMIT
        try:
            value = int(raw_limit)
        except ValueError as exc:
            raise ValueError("limit must be an integer") from exc
        if value < 1 or value > MAX_LIMIT:
            raise ValueError(f"limit must be between 1 and {MAX_LIMIT}")
        return value


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:  # pragma: no cover - AWS entry
    return JobListApplication().handle_event(event)
