from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional, Tuple

import boto3
from botocore.config import Config
from urllib.parse import urlparse
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
        self._s3 = s3_client

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
            self._attach_signed_urls(item)

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

    def _attach_signed_urls(self, item: Dict[str, Any]) -> None:
        review_metadata = item.get("review_metadata") or {}
        if not isinstance(review_metadata, dict):
            return

        narration = review_metadata.get("narration")
        if not isinstance(narration, dict):
            return

        bucket_hint = (
            item.get("output_bucket")
            or item.get("outputBucket")
            or review_metadata.get("output_bucket")
            or review_metadata.get("outputBucket")
        )
        prefix_hint = (
            item.get("output_prefix")
            or item.get("outputPrefix")
            or review_metadata.get("output_prefix")
            or review_metadata.get("outputPrefix")
        )

        for field in ("transcript_path", "audio_path", "alignment_path"):
            raw = narration.get(field)
            signed = self._sign_asset(raw, bucket_hint, prefix_hint)
            if signed:
                narration[field] = signed

    def _sign_asset(
        self,
        path: Any,
        bucket_hint: Optional[str],
        prefix_hint: Optional[str],
    ) -> Optional[str]:
        if not path or not isinstance(path, str):
            return None

        parsed = self._parse_bucket_key(path)
        if parsed is None:
            if bucket_hint and prefix_hint:
                normalized = path.lstrip("/")
                key = "/".join(part.strip("/") for part in (prefix_hint, normalized) if part)
                parsed = (bucket_hint, key)
            else:
                return None

        bucket, key = parsed
        if not bucket or not key:
            return None

        try:
            return self._ensure_s3().generate_presigned_url(
                "get_object",
                Params={"Bucket": bucket, "Key": key},
                ExpiresIn=900,
            )
        except Exception:
            logger.exception("Failed to generate presigned URL for %s/%s", bucket, key)
            return None

    @staticmethod
    def _parse_bucket_key(path: str) -> Optional[Tuple[str, str]]:
        trimmed = path.strip()
        if trimmed.startswith("s3://"):
            remainder = trimmed[5:]
            if "/" not in remainder:
                return None
            bucket, key = remainder.split("/", 1)
            return bucket, key

        if trimmed.startswith("http://") or trimmed.startswith("https://"):
            parsed = urlparse(trimmed)
            host = parsed.netloc
            if not host:
                return None
            if ".s3." in host:
                bucket = host.split(".s3.", 1)[0]
                key = parsed.path.lstrip("/")
                return bucket, key
            if host.endswith("amazonaws.com") and parsed.path.startswith("/"):
                parts = parsed.path.lstrip("/").split("/", 1)
                if len(parts) == 2:
                    return parts[0], parts[1]
            return None

        return None

    def _ensure_s3(self):  # type: ignore[return-value]
        if self._s3 is None:
            self._s3 = boto3.client("s3", config=Config(signature_version="s3v4"))
        return self._s3


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:  # pragma: no cover - AWS entry
    return JobListApplication().handle_event(event)
