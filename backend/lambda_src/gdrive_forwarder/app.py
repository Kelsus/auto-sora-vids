from __future__ import annotations

import logging
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
            parameter_name=self._settings.service_account_parameter,
            folder_id=self._settings.folder_id,
        )

    def handle(self, records: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
        processed = 0
        for record in records:
            bucket, key = self._extract_location(record)
            if not bucket or not key:
                logger.warning("Skipping record without bucket/key: %s", record)
                continue
            obj = self._storage.fetch(bucket, key)
            if obj is None:
                logger.error("Failed to fetch S3 object %s/%s", bucket, key)
                continue
            file_name = key.split("/")[-1]
            self._uploader.upload(file_name, obj.body)
            processed += 1
        return {"processed": processed}

    @staticmethod
    def _extract_location(record: Dict[str, Any]) -> tuple[str | None, str | None]:
        s3_info = record.get("s3", {})
        return (
            s3_info.get("bucket", {}).get("name"),
            s3_info.get("object", {}).get("key"),
        )


def lambda_handler(event: Dict[str, Any], _context: Any) -> Dict[str, Any]:  # pragma: no cover
    app = ForwarderApplication()
    return app.handle(event.get("Records", []))
