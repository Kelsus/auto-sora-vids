from __future__ import annotations

import sys
from pathlib import Path
from typing import Dict

root_dir = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root_dir / "backend" / "lambda_src"))
sys.path.insert(0, str(root_dir / "backend" / "lambda_src" / "common_layer" / "python"))

from gdrive_forwarder.app import ForwarderApplication
from gdrive_forwarder.settings import ForwarderSettings
from gdrive_forwarder.storage import S3Object


class StubStorage:
    def __init__(self, objects: Dict[str, S3Object]):
        self.objects = objects
        self.requests: list[tuple[str, str]] = []

    def fetch(self, bucket: str, key: str):
        self.requests.append((bucket, key))
        return self.objects.get(f"{bucket}:{key}")


class RecordingUploader:
    def __init__(self):
        self.uploads: list[tuple[str, bytes, str | None, str | None]] = []

    def upload(self, file_name: str, data: bytes, folder_name: str | None = None, mime_type: str | None = None) -> None:
        self.uploads.append((file_name, data, folder_name, mime_type))


def test_forwarder_uploads_video_and_metadata():
    settings = ForwarderSettings(service_account_parameter="parameter", folder_id="folder")
    storage = StubStorage(
        {
            "bucket:jobs/final/job-1/video.mp4": S3Object(
                bucket="bucket",
                key="jobs/final/job-1/video.mp4",
                body=b"video",
                metadata={"drive-folder": "DriveA"},
            ),
            "bucket:jobs/final/job-1/video.json": S3Object(
                bucket="bucket",
                key="jobs/final/job-1/video.json",
                body=b"{\"key\": \"value\"}",
                metadata={"drive-folder": "DriveA"},
            ),
        }
    )
    uploader = RecordingUploader()
    app = ForwarderApplication(settings=settings, storage=storage, uploader=uploader)

    event = {
        "Records": [
            {"s3": {"bucket": {"name": "bucket"}, "object": {"key": "jobs/final/job-1/video.mp4"}}},
            {"s3": {"bucket": {"name": "bucket"}, "object": {"key": "jobs/final/job-1/video.json"}}},
            {"s3": {"bucket": {}, "object": {}}},
        ]
    }

    response = app.handle(event["Records"])

    assert response == {"processed": 2}
    assert uploader.uploads == [
        ("video.mp4", b"video", "DriveA", "video/mp4"),
        ("video.json", b"{\"key\": \"value\"}", "DriveA", "application/json"),
    ]
    assert storage.requests[0] == ("bucket", "jobs/final/job-1/video.mp4")


def test_forwarder_uses_drive_folder_override():
    settings = ForwarderSettings(service_account_parameter="parameter", folder_id="default-folder")
    storage = StubStorage(
        {
            "bucket:jobs/final/job-2/job-video.mp4": S3Object(
                bucket="bucket",
                key="jobs/final/job-2/job-video.mp4",
                body=b"data",
                metadata={"drive-folder": "custom-folder"},
            )
        }
    )
    uploader = RecordingUploader()
    app = ForwarderApplication(settings=settings, storage=storage, uploader=uploader)

    event = {
        "Records": [
            {"s3": {"bucket": {"name": "bucket"}, "object": {"key": "jobs/final/job-2/job-video.mp4"}}}
        ]
    }

    response = app.handle(event["Records"])

    assert response == {"processed": 1}
    assert uploader.uploads == [("job-video.mp4", b"data", "custom-folder", "video/mp4")]
