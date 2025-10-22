from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from datetime import datetime, timezone

import pytest

root_dir = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root_dir / "backend" / "lambda_src"))
sys.path.insert(0, str(root_dir / "backend" / "lambda_src" / "common_layer" / "python"))

from job_ingest.app import JobIngestApplication
from job_ingest.http import HttpRequestParser
from job_ingest.models import JobRequest, ValidationError
from job_ingest.repository import JobRecord, PersistenceError


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
    assert job.job_type == "SCHEDULED"


def test_job_request_accepts_pipeline_config_override():
    payload = {
        "url": "https://example.com/story",
        "social_media": "tiktok",
        "scheduled_datetime": "2024-08-12T18:00:00Z",
        "pipelineConfig": {"media_provider": "veo", "sora_model": "sora-3"},
    }
    job = JobRequest.from_payload(payload)
    assert job.metadata["pipeline_config"] == {"media_provider": "veo", "sora_model": "sora-3"}


def test_job_request_missing_field():
    with pytest.raises(ValidationError):
        JobRequest.from_payload({"url": "https://example.com"})


def test_job_request_requires_schedule_for_scheduled():
    payload = {
        "url": "https://example.com/story",
        "social_media": "tiktok",
        "job_type": "SCHEDULED",
    }
    with pytest.raises(ValidationError):
        JobRequest.from_payload(payload)


def test_job_request_allows_immediate_without_schedule():
    payload = {
        "url": "https://example.com/story",
        "social_media": "tiktok",
        "job_type": "immediate",
    }
    job = JobRequest.from_payload(payload)
    assert job.job_type == "IMMEDIATE"
    assert job.scheduled_datetime.tzinfo == timezone.utc
    now = datetime.now(timezone.utc)
    assert now >= job.scheduled_datetime
    assert (now - job.scheduled_datetime).total_seconds() < 5


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
    assert record.job_type == "SCHEDULED"


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


def test_ingest_stores_immediate_job(monkeypatch):
    os.environ["JOBS_TABLE_NAME"] = "table"
    payload = {
        "url": "https://example.com/story",
        "social_media": "tiktok",
        "job_type": "immediate",
    }
    store = RecordingStore()
    app = JobIngestApplication(repository=store, request_parser=StubParser(payload))

    response = app.handle_event({"httpMethod": "POST", "body": payload})

    assert response["statusCode"] == 201
    assert store.records
    record = store.records[0]
    assert record.job_type == "IMMEDIATE"
    assert record.scheduled_datetime.tzinfo == timezone.utc
    assert (datetime.now(timezone.utc) - record.scheduled_datetime).total_seconds() < 5



def test_options_request_returns_preflight_response(monkeypatch):
    os.environ["JOBS_TABLE_NAME"] = "table"
    app = JobIngestApplication(repository=RecordingStore(), request_parser=StubParser({}))

    response = app.handle_event({"httpMethod": "OPTIONS"})

    assert response["statusCode"] == 204
