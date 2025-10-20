from __future__ import annotations

import json
import os
from datetime import timezone

import pytest

from infra.lambda_src.job_ingest.app import JobIngestApplication
from infra.lambda_src.job_ingest.http import HttpRequestParser
from infra.lambda_src.job_ingest.models import JobRequest, ValidationError
from infra.lambda_src.job_ingest.repository import JobRecord, PersistenceError


class StubParser(HttpRequestParser):
    def __init__(self, payload):
        self._payload = payload

    def parse(self, event):
        return self._payload


class RecordingStore:
    def __init__(self):
        self.records = []

    def save(self, record: JobRecord) -> None:
        self.records.append(record)


class FailingStore:
    def save(self, record: JobRecord) -> None:
        raise PersistenceError("boom")


def test_job_request_parsing_success():
    payload = {
        "url": "https://example.com/story",
        "social_media": "tiktok",
        "scheduled_datetime": "2024-08-12T18:00:00Z",
    }
    job = JobRequest.from_payload(payload)
    assert job.url == payload["url"]
    assert job.status == "PENDING"
    assert job.scheduled_datetime.tzinfo == timezone.utc


def test_job_request_missing_field():
    with pytest.raises(ValidationError):
        JobRequest.from_payload({"url": "https://example.com"})


def test_ingest_application_creates_job(monkeypatch):
    os.environ["JOBS_TABLE_NAME"] = "table"
    payload = {
        "url": "https://example.com/story",
        "social_media": "tiktok",
        "scheduled_datetime": "2024-08-12T18:00:00Z",
        "status": "pending",
        "metadata": {"topic": "news"},
    }
    store = RecordingStore()
    app = JobIngestApplication(repository=store, request_parser=StubParser(payload))

    response = app.handle_event({"httpMethod": "POST", "body": payload})

    assert response["statusCode"] == 201
    body = json.loads(response["body"])
    assert body["jobId"] == "https-example-com-story"
    assert store.records  # ensure save called
    record = store.records[0]
    assert record.url == payload["url"]
    assert record.scheduled_datetime.tzinfo == timezone.utc
    assert record.job_id == "https-example-com-story"


def test_ingest_returns_error_when_persistence_fails(monkeypatch):
    os.environ["JOBS_TABLE_NAME"] = "table"
    payload = {
        "url": "https://example.com/story",
        "social_media": "tiktok",
        "scheduled_datetime": "2024-08-12T18:00:00Z",
    }
    app = JobIngestApplication(repository=FailingStore(), request_parser=StubParser(payload))

    response = app.handle_event({"httpMethod": "POST", "body": payload})

    assert response["statusCode"] == 500


def test_options_request_returns_preflight_response(monkeypatch):
    os.environ["JOBS_TABLE_NAME"] = "table"
    app = JobIngestApplication(repository=RecordingStore(), request_parser=StubParser({}))

    response = app.handle_event({"httpMethod": "OPTIONS"})

    assert response["statusCode"] == 204
