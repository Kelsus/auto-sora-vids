from __future__ import annotations

import json
import logging
import mimetypes
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import unquote_plus

import boto3

from videopusher_forwarder.settings import ForwarderSettings

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Entity mapping from drive_folder values
ENTITY_MAP = {
    "korsair": "korsair",
    "kelsus": "kelsus",
    "searchclick": "searchclick",
}
DEFAULT_ENTITY = "korsair"


class VideoPusherForwarder:
    """Forwards completed videos from auto-sora-vids to VideoPusher uploads."""

    def __init__(
        self,
        settings: ForwarderSettings | None = None,
        s3_client: Any = None,
        dynamodb_client: Any = None,
    ) -> None:
        self._settings = settings or ForwarderSettings.from_env()
        self._s3 = s3_client or boto3.client("s3")
        self._dynamodb = dynamodb_client or boto3.resource("dynamodb")
        self._table = self._dynamodb.Table(self._settings.videopusher_table_name)

    def handle(self, records: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
        processed = 0
        errors = 0

        for record in records:
            bucket, key = self._extract_s3_location(record)
            if not bucket or not key:
                logger.warning("Skipping record without bucket/key: %s", record)
                continue

            # Only process MP4 files
            if not key.lower().endswith(".mp4"):
                logger.info("Skipping non-MP4 file: %s", key)
                continue

            # Only process files from jobs/final/{job_id}/
            if not self._is_final_video_key(key):
                logger.info("Skipping non-final video key: %s", key)
                continue

            try:
                self._process_video(bucket, key)
                processed += 1
            except Exception:
                logger.exception("Failed to process video %s/%s", bucket, key)
                errors += 1

        return {"processed": processed, "errors": errors}

    def _process_video(self, bucket: str, video_key: str) -> None:
        """Process a single video file and create a VideoPusher upload record."""
        job_id = self._extract_job_id(video_key)
        logger.info("Processing video for job %s: %s", job_id, video_key)

        # Find corresponding JSON metadata file
        json_key = self._find_metadata_key(bucket, video_key)
        if not json_key:
            logger.warning("No metadata JSON found for video %s", video_key)
            # Create minimal metadata
            raw_metadata = {}
        else:
            # Download and parse metadata
            raw_metadata = self._download_json(bucket, json_key)

        # Get entity from S3 object metadata
        s3_metadata = self._get_object_metadata(bucket, video_key)
        entity = self._derive_entity(s3_metadata)

        # Generate upload ID
        upload_id = str(uuid.uuid4())
        logger.info("Creating upload %s for job %s (entity: %s)", upload_id, job_id, entity)

        # Determine video extension
        video_ext = self._get_extension(video_key)

        # Copy video to VideoPusher bucket
        dest_video_key = f"raw/{upload_id}{video_ext}"
        self._copy_to_videopusher(bucket, video_key, dest_video_key, "video/mp4")

        # Copy thumbnail to VideoPusher bucket if available
        thumbnail_key = self._find_thumbnail_key(bucket, video_key)
        dest_cover_key: Optional[str] = None
        if thumbnail_key:
            dest_cover_key = f"covers/{upload_id}.png"
            self._copy_to_videopusher(bucket, thumbnail_key, dest_cover_key, "image/png")
            logger.info("Copied thumbnail %s to %s", thumbnail_key, dest_cover_key)

        # Copy or create metadata in VideoPusher bucket
        dest_manifest_key = f"manifests/{upload_id}.json"
        manifest_bytes = json.dumps(raw_metadata).encode("utf-8")
        self._upload_to_videopusher(manifest_bytes, dest_manifest_key, "application/json")

        # Transform metadata to VideoPusher format
        normalized_metadata = self._normalize_metadata(raw_metadata, entity)

        # Create DynamoDB record
        now = datetime.now(timezone.utc).isoformat()
        item: Dict[str, Any] = {
            "pk": f"UPLOAD#{upload_id}",
            "sk": f"UPLOAD#{upload_id}",
            "uploadId": upload_id,
            "s3VideoKey": dest_video_key,
            "s3ManifestKey": dest_manifest_key,
            "normalizedMetadata": normalized_metadata,
            "originalMetadata": raw_metadata,
            "entity": entity,
            "status": "Draft",
            "createdAt": now,
            "updatedAt": now,
            # Cross-reference to source
            "sourceJobId": job_id,
            "sourceS3Key": video_key,
        }
        if dest_cover_key:
            item["s3CoverImageKey"] = dest_cover_key

        self._table.put_item(Item=item)
        logger.info("Created upload record %s in VideoPusher", upload_id)

    def _is_final_video_key(self, key: str) -> bool:
        """Check if key matches jobs/final/{job_id}/{filename}.mp4 pattern."""
        if not key.startswith("jobs/final/"):
            return False
        # Ensure there's a job_id subdirectory (not just jobs/final/video.mp4)
        remainder = key[len("jobs/final/") :]
        return "/" in remainder

    def _extract_job_id(self, key: str) -> str:
        """Extract job_id from key like jobs/final/{job_id}/{filename}.mp4"""
        parts = key.split("/")
        if len(parts) >= 3 and parts[0] == "jobs" and parts[1] == "final":
            return parts[2]
        return "unknown"

    def _find_thumbnail_key(self, bucket: str, video_key: str) -> Optional[str]:
        """Find thumbnail.png in the same S3 directory as the video."""
        prefix = "/".join(video_key.split("/")[:-1]) + "/"
        thumbnail_key = f"{prefix}thumbnail.png"
        try:
            self._s3.head_object(Bucket=bucket, Key=thumbnail_key)
            return thumbnail_key
        except Exception:
            return None

    def _find_metadata_key(self, bucket: str, video_key: str) -> Optional[str]:
        """Find the corresponding .json metadata file for a video."""
        # Try replacing .mp4 with .json
        base_key = video_key.rsplit(".", 1)[0]
        json_key = f"{base_key}.json"

        try:
            self._s3.head_object(Bucket=bucket, Key=json_key)
            return json_key
        except self._s3.exceptions.ClientError:
            pass

        # List objects in the same directory to find any .json file
        prefix = "/".join(video_key.split("/")[:-1]) + "/"
        try:
            response = self._s3.list_objects_v2(Bucket=bucket, Prefix=prefix)
            for obj in response.get("Contents", []):
                if obj["Key"].endswith(".json"):
                    return obj["Key"]
        except Exception:
            logger.exception("Error listing objects for metadata search")

        return None

    def _download_json(self, bucket: str, key: str) -> Dict[str, Any]:
        """Download and parse a JSON file from S3."""
        response = self._s3.get_object(Bucket=bucket, Key=key)
        body = response["Body"].read()
        return json.loads(body.decode("utf-8"))

    def _get_object_metadata(self, bucket: str, key: str) -> Dict[str, str]:
        """Get S3 object user metadata."""
        try:
            response = self._s3.head_object(Bucket=bucket, Key=key)
            return response.get("Metadata", {})
        except Exception:
            logger.exception("Error getting object metadata for %s/%s", bucket, key)
            return {}

    def _derive_entity(self, s3_metadata: Dict[str, str]) -> str:
        """Derive entity from drive-folder S3 metadata tag."""
        drive_folder = s3_metadata.get("drive-folder", "").lower().strip()
        return ENTITY_MAP.get(drive_folder, DEFAULT_ENTITY)

    def _get_extension(self, key: str) -> str:
        """Get file extension from key."""
        if "." in key:
            return "." + key.rsplit(".", 1)[1].lower()
        return ".mp4"

    def _copy_to_videopusher(
        self, source_bucket: str, source_key: str, dest_key: str, content_type: str
    ) -> None:
        """Copy an object from source bucket to VideoPusher bucket."""
        copy_source = {"Bucket": source_bucket, "Key": source_key}
        self._s3.copy_object(
            CopySource=copy_source,
            Bucket=self._settings.videopusher_bucket_name,
            Key=dest_key,
            ContentType=content_type,
            MetadataDirective="REPLACE",
        )
        logger.info("Copied %s to %s/%s", source_key, self._settings.videopusher_bucket_name, dest_key)

    def _upload_to_videopusher(self, body: bytes, key: str, content_type: str) -> None:
        """Upload bytes to VideoPusher bucket."""
        self._s3.put_object(
            Bucket=self._settings.videopusher_bucket_name,
            Key=key,
            Body=body,
            ContentType=content_type,
        )
        logger.info("Uploaded %s to %s", key, self._settings.videopusher_bucket_name)

    def _normalize_metadata(self, raw: Dict[str, Any], entity: str) -> Dict[str, Any]:
        """Transform raw metadata to VideoPusher's normalized format."""
        normalized: Dict[str, Any] = {
            "title": raw.get("title", "Untitled"),
            "caption": raw.get("caption", ""),
            "entity": entity,
        }

        # Optional fields
        if raw.get("hashtags"):
            normalized["hashtags"] = raw["hashtags"]
        if raw.get("callToActionUrl"):
            normalized["callToActionUrl"] = raw["callToActionUrl"]
        if raw.get("allowLinkedInHashtags") is not None:
            normalized["allowLinkedInHashtags"] = raw["allowLinkedInHashtags"]
        if raw.get("scheduleAt"):
            normalized["scheduleAt"] = raw["scheduleAt"]
        if raw.get("perChannelOverrides"):
            normalized["perChannelOverrides"] = raw["perChannelOverrides"]
        if raw.get("videoLength"):
            normalized["videoLength"] = raw["videoLength"]
        if raw.get("videoStyle"):
            normalized["videoStyle"] = raw["videoStyle"]

        return normalized

    @staticmethod
    def _extract_s3_location(record: Dict[str, Any]) -> tuple[str | None, str | None]:
        """Extract bucket name and key from S3 event record.

        S3 event notifications URL-encode the object key (e.g. spaces become
        ``+``, non-ASCII bytes become ``%XX``), so we must decode it before
        using it in subsequent S3 API calls.
        """
        s3_info = record.get("s3", {})
        key = s3_info.get("object", {}).get("key")
        if key is not None:
            key = unquote_plus(key)
        return (
            s3_info.get("bucket", {}).get("name"),
            key,
        )
