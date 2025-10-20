from __future__ import annotations

from typing import Dict

from infra.lambda_src.gdrive_forwarder.app import ForwarderApplication
from infra.lambda_src.gdrive_forwarder.settings import ForwarderSettings
from infra.lambda_src.gdrive_forwarder.storage import S3Object


class StubStorage:
    def __init__(self, objects: Dict[str, S3Object]):
        self.objects = objects
        self.requests: list[tuple[str, str]] = []

    def fetch(self, bucket: str, key: str):
        self.requests.append((bucket, key))
        return self.objects.get(f"{bucket}:{key}")


class RecordingUploader:
    def __init__(self):
        self.uploads: list[tuple[str, bytes]] = []

    def upload(self, file_name: str, data: bytes) -> None:
        self.uploads.append((file_name, data))


def test_forwarder_uploads_videos():
    settings = ForwarderSettings(secret_name="secret", folder_id="folder")
    storage = StubStorage(
        {"bucket:jobs/final/video.mp4": S3Object(bucket="bucket", key="jobs/final/video.mp4", body=b"data")}
    )
    uploader = RecordingUploader()
    app = ForwarderApplication(settings=settings, storage=storage, uploader=uploader)

    event = {
        "Records": [
            {"s3": {"bucket": {"name": "bucket"}, "object": {"key": "jobs/final/video.mp4"}}},
            {"s3": {"bucket": {}, "object": {}}},
        ]
    }

    response = app.handle(event["Records"])

    assert response == {"processed": 1}
    assert uploader.uploads == [("video.mp4", b"data")]
    assert storage.requests[0] == ("bucket", "jobs/final/video.mp4")
