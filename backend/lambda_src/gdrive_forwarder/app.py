from __future__ import annotations

import logging
import mimetypes
from typing import Any, Dict, Iterable, List

from gdrive_forwarder.drive import DriveUploader
from gdrive_forwarder.settings import ForwarderSettings
from gdrive_forwarder.storage import S3Reader

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class ForwarderApplication:
    def __init__(
        self,
        settings: ForwarderSettings | None = None,
        uploader: DriveUploader | None = None,
        storage: S3Reader | None = None,
    ) -> None:
        self._settings = settings or ForwarderSettings.from_env()
        self._storage = storage or S3Reader()
        self._uploader = uploader or DriveUploader(
            service_account_parameter=self._settings.service_account_parameter,
            folder_identifier=self._settings.folder_id,
        )

    def handle(self, records: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
        processed = 0
        for record in records:
            bucket, key = self._extract_location(record)
            if not bucket or not key:
                logger.warning("Skipping record without bucket/key: %s", record)
                continue
            if self._is_legacy_final_key(key):
                logger.info("Skipping legacy final artifact key %s to avoid duplicate uploads", key)
                continue
            obj = self._storage.fetch(bucket, key)
            if obj is None:
                logger.error("Failed to fetch S3 object %s/%s", bucket, key)
                continue
            file_name = key.split("/")[-1]
            if not file_name.lower().endswith((".mp4", ".json")):
                logger.info("Skipping unsupported file type %s from %s/%s", file_name, bucket, key)
                continue
            mime_type, _ = mimetypes.guess_type(file_name)
            if not mime_type and file_name.lower().endswith(".json"):
                mime_type = "application/json"
            if not mime_type and file_name.lower().endswith(".mp4"):
                mime_type = "video/mp4"
            metadata = obj.metadata or {}
            drive_folder = metadata.get("drive-folder") or metadata.get("drive_folder")
            folder_name = drive_folder.strip() if isinstance(drive_folder, str) else None
            if folder_name:
                logger.info("Uploading %s to Drive subfolder '%s'", file_name, folder_name)
            self._uploader.upload(file_name, obj.body, folder_name=folder_name, mime_type=mime_type)
            processed += 1
        return {"processed": processed}

    @staticmethod
    def _extract_location(record: Dict[str, Any]) -> tuple[str | None, str | None]:
        s3_info = record.get("s3", {})
        return (
            s3_info.get("bucket", {}).get("name"),
            s3_info.get("object", {}).get("key"),
        )

    @staticmethod
    def _is_legacy_final_key(key: str) -> bool:
        prefix = "jobs/final/"
        if not key.startswith(prefix):
            return False
        remainder = key[len(prefix) :]
        return "/" not in remainder


def lambda_handler(event: Dict[str, Any], _context: Any) -> Dict[str, Any]:  # pragma: no cover
    app = ForwarderApplication()
    return app.handle(event.get("Records", []))
