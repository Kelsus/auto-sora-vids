from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import requests

from aivideomaker.media_pipeline.sora_client import SoraClient


class FakeResponse:
    def __init__(self, status_code: int, payload: bytes = b"ok") -> None:
        self.status_code = status_code
        self._payload = payload
        self.headers: dict[str, str] = {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(response=self)

    def iter_content(self, chunk_size: int):  # pragma: no cover - trivial generator
        yield self._payload


def test_download_video_retries_on_server_error(tmp_path, monkeypatch) -> None:
    target = tmp_path / "clip.mp4"
    responses = iter([
        FakeResponse(503),
        FakeResponse(502),
        FakeResponse(200, b"data"),
    ])

    def fake_get(url, headers, stream, timeout, params):  # noqa: ANN001
        return next(responses)

    monkeypatch.setattr(requests, "get", fake_get)
    monkeypatch.setattr("time.sleep", lambda *_args, **_kwargs: None)

    client = SoraClient(asset_dir=tmp_path, api_key="sk-test", download_max_attempts=5)
    client._download_video("job-123", target)
    assert target.read_bytes() == b"data"
