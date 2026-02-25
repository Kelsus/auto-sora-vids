from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlparse

import boto3
from botocore.config import Config

logger = logging.getLogger(__name__)

_PRESIGN_EXPIRY = 900


class JobItemSigner:
    """Attaches presigned S3 URLs to job items returned by the API."""

    def __init__(self, s3_client: Any | None = None) -> None:
        self._s3 = s3_client

    def attach_signed_urls(self, item: Dict[str, Any]) -> None:
        bucket_hint = item.get("output_bucket") or item.get("outputBucket")

        review_metadata = item.get("review_metadata") or {}
        if not isinstance(review_metadata, dict):
            review_metadata = {}

        bucket_hint = bucket_hint or review_metadata.get("output_bucket") or review_metadata.get("outputBucket")
        prefix_hint = (
            item.get("output_prefix")
            or item.get("outputPrefix")
            or review_metadata.get("output_prefix")
            or review_metadata.get("outputPrefix")
        )

        # Sign thumbnail: use thumbnail_key (COMPLETED) or review_metadata path (REVIEW)
        thumbnail_key = item.get("thumbnail_key")
        if thumbnail_key and bucket_hint:
            signed = self._sign_s3_key(bucket_hint, thumbnail_key)
            if signed:
                item["thumbnail_url"] = signed
        elif bucket_hint and prefix_hint:
            thumb_path = review_metadata.get("thumbnail_path")
            if thumb_path and isinstance(thumb_path, str):
                signed = self._sign_asset(thumb_path, bucket_hint, prefix_hint)
                if signed:
                    item["thumbnail_url"] = signed

        narration = review_metadata.get("narration")
        if not isinstance(narration, dict):
            return

        for field in ("transcript_path", "audio_path", "alignment_path"):
            raw = narration.get(field)
            signed = self._sign_asset(raw, bucket_hint, prefix_hint)
            if signed:
                narration[field] = signed

    def _sign_s3_key(self, bucket: str, key: str) -> Optional[str]:
        try:
            return self._ensure_s3().generate_presigned_url(
                "get_object",
                Params={"Bucket": bucket, "Key": key},
                ExpiresIn=_PRESIGN_EXPIRY,
            )
        except Exception:
            logger.exception("Failed to generate presigned URL for %s/%s", bucket, key)
            return None

    def _sign_asset(
        self,
        path: Any,
        bucket_hint: Optional[str],
        prefix_hint: Optional[str],
    ) -> Optional[str]:
        if not path or not isinstance(path, str):
            return None

        parsed = _parse_bucket_key(path)
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
                ExpiresIn=_PRESIGN_EXPIRY,
            )
        except Exception:
            logger.exception("Failed to generate presigned URL for %s/%s", bucket, key)
            return None

    def _ensure_s3(self):  # type: ignore[return-value]
        if self._s3 is None:
            self._s3 = boto3.client("s3", config=Config(signature_version="s3v4"))
        return self._s3


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
