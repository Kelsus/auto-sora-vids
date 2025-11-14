from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional, Tuple

import boto3
from botocore.config import Config
from urllib.parse import urlparse

from job_lookup.http import bad_request, cors_preflight_response, not_found, ok, server_error
from job_lookup.repository import JobLookupStore, LookupError

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class JobLookupApplication:
    """Handles retrieval of job metadata by ID."""

    def __init__(self, repository: JobLookupStore | None = None) -> None:
        table_name = os.environ["JOBS_TABLE_NAME"]
        self._repository = repository or JobLookupStore(table_name)
        self._s3 = boto3.client("s3", config=Config(signature_version="s3v4"))

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

        self._attach_signed_urls(item)
        return ok(item)

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
            return self._s3.generate_presigned_url(
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

    @staticmethod
    def _extract_job_id(event: Dict[str, Any]) -> str | None:
        path_params = event.get("pathParameters") or {}
        job_id = path_params.get("jobId")
        if job_id and job_id.strip():
            return job_id.strip()
        return None


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:  # pragma: no cover - AWS entry
    return JobLookupApplication().handle_event(event)
