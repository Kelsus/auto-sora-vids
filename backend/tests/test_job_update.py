from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import pytest

from common import RepositoryError
from common.jobs_repository import JobNotFoundError
from job_update.app import JobUpdateApplication
from job_update.cancellation import CancellationError, InvalidCancellationState
from job_update.models import JobUpdatePayload
from job_update.store import JobDoesNotExist, JobUpdateStore, UpdateError


class StubStore:
    def __init__(self, response: Optional[dict[str, Any]] = None, error: Exception | None = None) -> None:
        self.response = response or {"jobId": "abc", "status": "QUEUED"}
        self.error = error
        self.job_id: Optional[str] = None
        self.payload: Optional[JobUpdatePayload] = None

    def update_job(self, job_id: str, payload: JobUpdatePayload) -> dict[str, Any]:
        self.job_id = job_id
        self.payload = payload
        if self.error:
            raise self.error
        return self.response


class StubCancellationService:
    def __init__(self, response: Optional[dict[str, Any]] = None, error: Exception | None = None) -> None:
        self.response = response or {"jobId": "abc", "status": "CANCELED"}
        self.error = error
        self.job_id: Optional[str] = None

    def cancel(self, job_id: str) -> dict[str, Any]:
        self.job_id = job_id
        if self.error:
            raise self.error
        return self.response


class StubRepository:
    def __init__(
        self,
        item: Optional[dict[str, Any]] = None,
        error: Exception | None = None,
        update_error: Exception | None = None,
    ) -> None:
        self.item = item
        self.error = error
        self.update_error = update_error
        self.requested: list[str] = []
        self.updates: list[tuple[str, str, Dict[str, Any]]] = []

    def get_job(self, job_id: str) -> Optional[dict[str, Any]]:
        self.requested.append(job_id)
        if self.error:
            raise self.error
        if self.item and self.item.get("jobId") == job_id:
            return dict(self.item)
        return None

    def update_status(self, job_id: str, status: str, attributes: Dict[str, Any]) -> None:
        if self.update_error:
            raise self.update_error
        self.updates.append((job_id, status, attributes))
        record = dict(self.item) if self.item and self.item.get("jobId") == job_id else {"jobId": job_id}
        record["status"] = status
        for key, value in attributes.items():
            if value is None:
                record.pop(key, None)
            else:
                record[key] = value
        self.item = record


class StubPaginator:
    def __init__(self, pages: list[dict[str, Any]]) -> None:
        self.pages = pages
        self.kwargs: list[dict[str, Any]] = []

    def paginate(self, **kwargs):
        self.kwargs.append(kwargs)
        for page in self.pages:
            yield page


class StubS3Client:
    def __init__(self, pages: Optional[list[dict[str, Any]]] = None, should_fail: bool = False) -> None:
        self.pages = pages or []
        self.deleted: list[dict[str, Any]] = []
        self.should_fail = should_fail

    def get_paginator(self, name: str) -> StubPaginator:
        assert name == "list_objects_v2"
        return StubPaginator(self.pages)

    def delete_objects(self, **kwargs) -> None:
        if self.should_fail:
            raise RuntimeError("delete failed")
        self.deleted.append(kwargs)


def build_app(
    store: StubStore,
    *,
    cancel_service: StubCancellationService | None = None,
    repository: StubRepository | None = None,
    s3_client: StubS3Client | None = None,
) -> JobUpdateApplication:
    repository = repository or StubRepository()
    s3_client = s3_client or StubS3Client()
    return JobUpdateApplication(
        store=store,
        cancellation_service=cancel_service,
        repository=repository,
        s3_client=s3_client,
    )


def test_updates_job(monkeypatch: pytest.MonkeyPatch) -> None:
    store = StubStore(response={"jobId": "abc", "status": "RUNNING"})
    monkeypatch.setenv("JOBS_TABLE_NAME", "jobs")
    app = build_app(store)

    event = {
        "httpMethod": "PATCH",
        "pathParameters": {"jobId": "abc"},
        "body": json.dumps({"status": "RUNNING"}),
    }
    response = app.handle_event(event)

    assert response["statusCode"] == 200
    assert response["body"] == '{"jobId": "abc", "status": "RUNNING"}'
    assert store.job_id == "abc"
    assert store.payload is not None
    assert store.payload.status == "RUNNING"


def test_returns_bad_request_when_no_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    store = StubStore()
    monkeypatch.setenv("JOBS_TABLE_NAME", "jobs")
    app = build_app(store)

    event = {
        "httpMethod": "PATCH",
        "pathParameters": {"jobId": "abc"},
        "body": json.dumps({}),
    }
    response = app.handle_event(event)

    assert response["statusCode"] == 400


def test_cancel_job_updates_status(monkeypatch: pytest.MonkeyPatch) -> None:
    store = StubStore()
    cancel_service = StubCancellationService(response={"jobId": "abc", "status": "CANCELED"})
    monkeypatch.setenv("JOBS_TABLE_NAME", "jobs")
    app = build_app(store, cancel_service=cancel_service)

    event = {
        "httpMethod": "PATCH",
        "pathParameters": {"jobId": "abc"},
        "body": json.dumps({"status": "canceled"}),
    }
    response = app.handle_event(event)

    assert response["statusCode"] == 200
    assert cancel_service.job_id == "abc"
    assert store.job_id is None


def test_cancel_job_with_extra_fields_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    store = StubStore()
    cancel_service = StubCancellationService()
    monkeypatch.setenv("JOBS_TABLE_NAME", "jobs")
    app = build_app(store, cancel_service=cancel_service)

    event = {
        "httpMethod": "PATCH",
        "pathParameters": {"jobId": "abc"},
        "body": json.dumps({"status": "CANCELED", "job_type": "IMMEDIATE"}),
    }
    response = app.handle_event(event)

    assert response["statusCode"] == 400
    assert cancel_service.job_id is None


def test_cancel_job_handles_invalid_state(monkeypatch: pytest.MonkeyPatch) -> None:
    store = StubStore()
    cancel_service = StubCancellationService(error=InvalidCancellationState("Cannot cancel job"))
    monkeypatch.setenv("JOBS_TABLE_NAME", "jobs")
    app = build_app(store, cancel_service=cancel_service)

    event = {
        "httpMethod": "PATCH",
        "pathParameters": {"jobId": "abc"},
        "body": json.dumps({"status": "CANCELED"}),
    }
    response = app.handle_event(event)

    assert response["statusCode"] == 409


def test_cancel_job_handles_cancellation_error(monkeypatch: pytest.MonkeyPatch) -> None:
    store = StubStore()
    cancel_service = StubCancellationService(error=CancellationError("boom"))
    monkeypatch.setenv("JOBS_TABLE_NAME", "jobs")
    app = build_app(store, cancel_service=cancel_service)

    event = {
        "httpMethod": "PATCH",
        "pathParameters": {"jobId": "abc"},
        "body": json.dumps({"status": "CANCELED"}),
    }
    response = app.handle_event(event)

    assert response["statusCode"] == 500


def test_cancel_job_when_service_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    store = StubStore()
    monkeypatch.setenv("JOBS_TABLE_NAME", "jobs")
    if "STATE_MACHINE_ARN" in os.environ:
        monkeypatch.delenv("STATE_MACHINE_ARN", raising=False)
    app = build_app(store, cancel_service=None)

    event = {
        "httpMethod": "PATCH",
        "pathParameters": {"jobId": "abc"},
        "body": json.dumps({"status": "CANCELED"}),
    }
    response = app.handle_event(event)

    assert response["statusCode"] == 500


def test_returns_not_found_when_job_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    store = StubStore(error=JobDoesNotExist("missing"))
    monkeypatch.setenv("JOBS_TABLE_NAME", "jobs")
    app = JobUpdateApplication(store=store)

    event = {
        "httpMethod": "PATCH",
        "pathParameters": {"jobId": "abc"},
        "body": json.dumps({"status": "RUNNING"}),
    }
    response = app.handle_event(event)

    assert response["statusCode"] == 404


def test_returns_server_error_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    store = StubStore(error=UpdateError("boom"))
    monkeypatch.setenv("JOBS_TABLE_NAME", "jobs")
    app = JobUpdateApplication(store=store)

    event = {
        "httpMethod": "PATCH",
        "pathParameters": {"jobId": "abc"},
        "body": json.dumps({"status": "RUNNING"}),
    }
    response = app.handle_event(event)

    assert response["statusCode"] == 500


def test_handles_preflight(monkeypatch: pytest.MonkeyPatch) -> None:
    store = StubStore()
    monkeypatch.setenv("JOBS_TABLE_NAME", "jobs")
    app = JobUpdateApplication(store=store)

    event = {
        "httpMethod": "OPTIONS",
        "pathParameters": {"jobId": "abc"},
        "body": None,
    }
    response = app.handle_event(event)

    assert response["statusCode"] == 204


def test_completed_to_pending_deletes_artifacts(monkeypatch: pytest.MonkeyPatch) -> None:
    store = StubStore(response={"jobId": "abc", "status": "PENDING"})
    job = {
        "jobId": "abc",
        "status": "COMPLETED",
        "output_bucket": "media-bucket",
        "output_prefix": "jobs/abc/",
        "final_video_key": "jobs/final/abc.mp4",
    }
    repository = StubRepository(item=job)
    s3 = StubS3Client(pages=[{"Contents": [{"Key": "jobs/abc/run/file1"}]}])
    monkeypatch.setenv("JOBS_TABLE_NAME", "jobs")
    app = build_app(store, repository=repository, s3_client=s3)

    event = {
        "httpMethod": "PATCH",
        "pathParameters": {"jobId": "abc"},
        "body": json.dumps({"status": "PENDING"}),
    }
    response = app.handle_event(event)

    assert response["statusCode"] == 200
    assert s3.deleted  # prefix and key deletions attempted
    assert repository.requested == ["abc"]


def test_failed_to_pending_deletes_artifacts_when_flag_true(monkeypatch: pytest.MonkeyPatch) -> None:
    store = StubStore(response={"jobId": "abc", "status": "PENDING"})
    job = {
        "jobId": "abc",
        "status": "FAILED",
        "output_bucket": "media-bucket",
        "output_prefix": "jobs/abc/",
    }
    repository = StubRepository(item=job)
    s3 = StubS3Client(pages=[{"Contents": [{"Key": "jobs/abc/run/file1"}]}])
    monkeypatch.setenv("JOBS_TABLE_NAME", "jobs")
    app = build_app(store, repository=repository, s3_client=s3)

    event = {
        "httpMethod": "PATCH",
        "pathParameters": {"jobId": "abc"},
        "body": json.dumps({"status": "PENDING", "delete_artifacts": True}),
    }
    response = app.handle_event(event)

    assert response["statusCode"] == 200
    assert s3.deleted  # cleanup performed


def test_failed_to_pending_skips_artifact_deletion_when_flag_false(monkeypatch: pytest.MonkeyPatch) -> None:
    store = StubStore(response={"jobId": "abc", "status": "PENDING"})
    job = {
        "jobId": "abc",
        "status": "FAILED",
        "output_bucket": "media-bucket",
    }
    repository = StubRepository(item=job)
    s3 = StubS3Client()
    monkeypatch.setenv("JOBS_TABLE_NAME", "jobs")
    app = build_app(store, repository=repository, s3_client=s3)

    event = {
        "httpMethod": "PATCH",
        "pathParameters": {"jobId": "abc"},
        "body": json.dumps({"status": "PENDING", "delete_artifacts": False}),
    }
    response = app.handle_event(event)

    assert response["statusCode"] == 200
    assert s3.deleted == []


def test_delete_artifacts_without_pending_status_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    store = StubStore()
    repository = StubRepository()
    s3 = StubS3Client()
    monkeypatch.setenv("JOBS_TABLE_NAME", "jobs")
    app = build_app(store, repository=repository, s3_client=s3)

    event = {
        "httpMethod": "PATCH",
        "pathParameters": {"jobId": "abc"},
        "body": json.dumps({"delete_artifacts": True}),
    }
    response = app.handle_event(event)

    assert response["statusCode"] == 400
    assert store.job_id is None


def test_pending_transition_with_invalid_previous_state_returns_bad_request(monkeypatch: pytest.MonkeyPatch) -> None:
    store = StubStore()
    job = {"jobId": "abc", "status": "RUNNING", "output_bucket": "media-bucket"}
    repository = StubRepository(item=job)
    s3 = StubS3Client()
    monkeypatch.setenv("JOBS_TABLE_NAME", "jobs")
    app = build_app(store, repository=repository, s3_client=s3)

    event = {
        "httpMethod": "PATCH",
        "pathParameters": {"jobId": "abc"},
        "body": json.dumps({"status": "PENDING", "delete_artifacts": True}),
    }
    response = app.handle_event(event)

    assert response["statusCode"] == 400
    assert store.job_id is None
    assert s3.deleted == []


def test_pending_transition_missing_job_returns_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    store = StubStore()
    repository = StubRepository(item=None)
    s3 = StubS3Client()
    monkeypatch.setenv("JOBS_TABLE_NAME", "jobs")
    app = build_app(store, repository=repository, s3_client=s3)

    event = {
        "httpMethod": "PATCH",
        "pathParameters": {"jobId": "abc"},
        "body": json.dumps({"status": "PENDING"}),
    }
    response = app.handle_event(event)

    assert response["statusCode"] == 404
    assert store.job_id is None


def test_pending_transition_repository_failure_returns_server_error(monkeypatch: pytest.MonkeyPatch) -> None:
    store = StubStore()
    repository = StubRepository(error=RepositoryError("boom"))
    s3 = StubS3Client()
    monkeypatch.setenv("JOBS_TABLE_NAME", "jobs")
    app = build_app(store, repository=repository, s3_client=s3)

    event = {
        "httpMethod": "PATCH",
        "pathParameters": {"jobId": "abc"},
        "body": json.dumps({"status": "PENDING"}),
    }
    response = app.handle_event(event)

    assert response["statusCode"] == 500
    assert store.job_id is None


def test_pending_transition_artifact_cleanup_failure_returns_server_error(monkeypatch: pytest.MonkeyPatch) -> None:
    store = StubStore()
    job = {
        "jobId": "abc",
        "status": "COMPLETED",
        "output_bucket": "media-bucket",
        "final_video_key": "jobs/final/abc.mp4",
    }
    repository = StubRepository(item=job)
    s3 = StubS3Client(should_fail=True)
    monkeypatch.setenv("JOBS_TABLE_NAME", "jobs")
    app = build_app(store, repository=repository, s3_client=s3)

    event = {
        "httpMethod": "PATCH",
        "pathParameters": {"jobId": "abc"},
        "body": json.dumps({"status": "PENDING"}),
    }
    response = app.handle_event(event)

    assert response["statusCode"] == 500
    assert store.job_id is None


class FakeRepository:
    def __init__(self) -> None:
        self.updated: Dict[str, Any] | None = None
        self.last_set_parts: Optional[list[str]] = None
        self.last_remove: Optional[list[str]] = None
        self.last_names: Optional[Dict[str, str]] = None
        self.last_values: Optional[Dict[str, Any]] = None
        self.item: dict[str, Any] | None = {
            "jobId": "abc",
            "status": "PENDING",
            "metadata": {},
        }

    def update_fields(
        self,
        job_id: str,
        set_parts: list[str],
        attribute_names: Dict[str, str],
        attribute_values: Dict[str, Any],
        remove_parts: Optional[list[str]] = None,
    ) -> None:
        if job_id != "abc":
            raise JobNotFoundError("missing")
        self.last_set_parts = set_parts
        self.last_remove = remove_parts
        self.last_names = attribute_names
        self.last_values = attribute_values
        # emulate status update
        if ":status" in attribute_values:
            self.item["status"] = attribute_values[":status"]

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        if job_id != "abc":
            return None
        return self.item


def test_store_updates_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = FakeRepository()
    store = JobUpdateStore("jobs", repository=repo)  # type: ignore[arg-type]

    payload = JobUpdatePayload.from_dict(
        {
            "status": "RUNNING",
            "job_type": "IMMEDIATE",
            "scheduled_datetime": datetime(2025, 10, 20, 12, 0, tzinfo=timezone.utc).isoformat(),
            "pipeline_config": {"drive_folder": "Ops"},
        }
    )

    result = store.update_job("abc", payload)

    assert result["status"] == "RUNNING"
    assert repo.last_set_parts is not None
    assert any("#status = :status" in part for part in repo.last_set_parts)
    assert repo.last_remove is None


def test_store_removes_pipeline_config(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = FakeRepository()
    store = JobUpdateStore("jobs", repository=repo)  # type: ignore[arg-type]
    payload = JobUpdatePayload.from_dict({"pipeline_config": None})

    result = store.update_job("abc", payload)

    assert result["jobId"] == "abc"
    assert repo.last_remove is not None
    assert "#metadata.#pipeline_config" in repo.last_remove


def test_store_raises_when_job_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    class MissingRepo(FakeRepository):
        def update_fields(self, *args, **kwargs):
            raise JobNotFoundError("missing")

    store = JobUpdateStore("jobs", repository=MissingRepo())  # type: ignore[arg-type]
    payload = JobUpdatePayload.from_dict({"status": "RUNNING"})

    with pytest.raises(JobDoesNotExist):
        store.update_job("abc", payload)


def test_store_wraps_repository_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    class ErrorRepo(FakeRepository):
        def update_fields(self, *args, **kwargs):
            raise RepositoryError("boom")

    store = JobUpdateStore("jobs", repository=ErrorRepo())  # type: ignore[arg-type]
    payload = JobUpdatePayload.from_dict({"status": "RUNNING"})

    with pytest.raises(UpdateError):
        store.update_job("abc", payload)


def test_payload_normalizes_status() -> None:
    payload = JobUpdatePayload.from_dict({"status": "canceled"})
    assert payload.status == "CANCELED"


def test_payload_rejects_unknown_status() -> None:
    with pytest.raises(ValueError):
        JobUpdatePayload.from_dict({"status": "paused"})


def test_payload_uppercases_job_type() -> None:
    payload = JobUpdatePayload.from_dict({"job_type": "immediate"})
    assert payload.job_type == "IMMEDIATE"


def test_payload_rejects_non_boolean_delete_artifacts() -> None:
    with pytest.raises(ValueError):
        JobUpdatePayload.from_dict({"status": "PENDING", "delete_artifacts": "yes"})
