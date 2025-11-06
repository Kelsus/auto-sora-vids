from __future__ import annotations

from typing import Any, Optional

import pytest

from common import RepositoryError
from common.jobs_repository import JobNotFoundError
from job_delete.app import JobDeleteApplication
from job_delete.store import DeleteError, JobDeleteStore, JobDoesNotExist


class StubStore:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.job_id: Optional[str] = None

    def delete_job(self, job_id: str) -> None:
        self.job_id = job_id
        if self.error:
            raise self.error


class StubRepository:
    def __init__(self, item: Optional[dict[str, Any]] = None, error: Exception | None = None) -> None:
        self.item = item
        self.error = error
        self.requested: list[str] = []

    def get_job(self, job_id: str) -> Optional[dict[str, Any]]:
        self.requested.append(job_id)
        if self.error:
            raise self.error
        if self.item and self.item.get("jobId") == job_id:
            return dict(self.item)
        return None


class StubPaginator:
    def __init__(self, pages: Optional[list[dict[str, Any]]] = None) -> None:
        self.pages = pages or []
        self.requests: list[dict[str, Any]] = []

    def paginate(self, **kwargs):
        self.requests.append(kwargs)
        for page in self.pages:
            yield page


class StubS3Client:
    def __init__(self, pages: Optional[list[dict[str, Any]]] = None, should_fail: bool = False) -> None:
        self._paginator = StubPaginator(pages)
        self.should_fail = should_fail
        self.deleted: list[dict[str, Any]] = []

    def get_paginator(self, name: str) -> StubPaginator:
        assert name == "list_objects_v2"
        return self._paginator

    def delete_objects(self, **kwargs) -> None:
        if self.should_fail:
            raise RuntimeError("boom")
        self.deleted.append(kwargs)


def test_deletes_job(monkeypatch: pytest.MonkeyPatch) -> None:
    store = StubStore()
    monkeypatch.setenv("JOBS_TABLE_NAME", "jobs")
    app = JobDeleteApplication(store=store)

    event = {
        "httpMethod": "DELETE",
        "pathParameters": {"jobId": "abc"},
    }
    response = app.handle_event(event)

    assert response["statusCode"] == 204
    assert response["body"] == ""
    assert store.job_id == "abc"


def test_returns_bad_request_without_job_id(monkeypatch: pytest.MonkeyPatch) -> None:
    store = StubStore()
    monkeypatch.setenv("JOBS_TABLE_NAME", "jobs")
    app = JobDeleteApplication(store=store)

    event = {
        "httpMethod": "DELETE",
        "pathParameters": {},
    }
    response = app.handle_event(event)

    assert response["statusCode"] == 400


def test_returns_not_found_when_job_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    store = StubStore(error=JobDoesNotExist("missing"))
    monkeypatch.setenv("JOBS_TABLE_NAME", "jobs")
    app = JobDeleteApplication(store=store)

    event = {
        "httpMethod": "DELETE",
        "pathParameters": {"jobId": "abc"},
    }
    response = app.handle_event(event)

    assert response["statusCode"] == 404


def test_returns_server_error_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    store = StubStore(error=DeleteError("boom"))
    monkeypatch.setenv("JOBS_TABLE_NAME", "jobs")
    app = JobDeleteApplication(store=store)

    event = {
        "httpMethod": "DELETE",
        "pathParameters": {"jobId": "abc"},
    }
    response = app.handle_event(event)

    assert response["statusCode"] == 500


def test_handles_preflight(monkeypatch: pytest.MonkeyPatch) -> None:
    store = StubStore()
    monkeypatch.setenv("JOBS_TABLE_NAME", "jobs")
    app = JobDeleteApplication(store=store)

    event = {
        "httpMethod": "OPTIONS",
        "pathParameters": {"jobId": "abc"},
    }
    response = app.handle_event(event)

    assert response["statusCode"] == 204


def test_delete_job_with_artifacts_removes_s3_objects(monkeypatch: pytest.MonkeyPatch) -> None:
    store = StubStore()
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
    app = JobDeleteApplication(store=store, repository=repository, s3_client=s3)

    event = {
        "httpMethod": "DELETE",
        "pathParameters": {"jobId": "abc"},
        "queryStringParameters": {"delete_artifacts": "true"},
    }
    response = app.handle_event(event)

    assert response["statusCode"] == 204
    assert store.job_id == "abc"
    assert repository.requested == ["abc"]
    assert s3.deleted  # at least one delete_objects call occurred


def test_delete_job_with_artifacts_missing_job_returns_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    store = StubStore()
    repository = StubRepository(item=None)
    s3 = StubS3Client()
    monkeypatch.setenv("JOBS_TABLE_NAME", "jobs")
    app = JobDeleteApplication(store=store, repository=repository, s3_client=s3)

    event = {
        "httpMethod": "DELETE",
        "pathParameters": {"jobId": "abc"},
        "queryStringParameters": {"delete_artifacts": "true"},
    }
    response = app.handle_event(event)

    assert response["statusCode"] == 404
    assert store.job_id is None
    assert s3.deleted == []


def test_delete_job_with_artifacts_repository_failure_returns_server_error(monkeypatch: pytest.MonkeyPatch) -> None:
    store = StubStore()
    repository = StubRepository(error=RepositoryError("boom"))
    s3 = StubS3Client()
    monkeypatch.setenv("JOBS_TABLE_NAME", "jobs")
    app = JobDeleteApplication(store=store, repository=repository, s3_client=s3)

    event = {
        "httpMethod": "DELETE",
        "pathParameters": {"jobId": "abc"},
        "queryStringParameters": {"delete_artifacts": "true"},
    }
    response = app.handle_event(event)

    assert response["statusCode"] == 500
    assert store.job_id is None
    assert repository.requested == ["abc"]


def test_delete_job_with_artifacts_cleanup_failure_returns_server_error(monkeypatch: pytest.MonkeyPatch) -> None:
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
    app = JobDeleteApplication(store=store, repository=repository, s3_client=s3)

    event = {
        "httpMethod": "DELETE",
        "pathParameters": {"jobId": "abc"},
        "queryStringParameters": {"delete_artifacts": "true"},
    }
    response = app.handle_event(event)

    assert response["statusCode"] == 500
    assert store.job_id is None
    assert s3.deleted == []  # failure occurs on first call


def test_delete_job_rejects_invalid_delete_artifacts_value(monkeypatch: pytest.MonkeyPatch) -> None:
    store = StubStore()
    repository = StubRepository()
    s3 = StubS3Client()
    monkeypatch.setenv("JOBS_TABLE_NAME", "jobs")
    app = JobDeleteApplication(store=store, repository=repository, s3_client=s3)

    event = {
        "httpMethod": "DELETE",
        "pathParameters": {"jobId": "abc"},
        "queryStringParameters": {"delete_artifacts": "maybe"},
    }
    response = app.handle_event(event)

    assert response["statusCode"] == 400
    assert store.job_id is None
    assert repository.requested == []


def test_delete_job_without_flag_skips_artifact_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    store = StubStore()
    repository = StubRepository()
    s3 = StubS3Client()
    monkeypatch.setenv("JOBS_TABLE_NAME", "jobs")
    app = JobDeleteApplication(store=store, repository=repository, s3_client=s3)

    event = {
        "httpMethod": "DELETE",
        "pathParameters": {"jobId": "abc"},
    }
    response = app.handle_event(event)

    assert response["statusCode"] == 204
    assert repository.requested == []  # no lookup performed


class FakeRepository:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.deleted_job_id: Optional[str] = None

    def delete_job(self, job_id: str) -> None:
        self.deleted_job_id = job_id
        if self.error:
            raise self.error


def test_store_deletes_job() -> None:
    repo = FakeRepository()
    store = JobDeleteStore("jobs", repository=repo)  # type: ignore[arg-type]

    store.delete_job("abc")

    assert repo.deleted_job_id == "abc"


def test_store_raises_when_job_missing() -> None:
    repo = FakeRepository(error=JobNotFoundError("missing"))
    store = JobDeleteStore("jobs", repository=repo)  # type: ignore[arg-type]

    with pytest.raises(JobDoesNotExist):
        store.delete_job("abc")


def test_store_wraps_repository_errors() -> None:
    repo = FakeRepository(error=RepositoryError("boom"))
    store = JobDeleteStore("jobs", repository=repo)  # type: ignore[arg-type]

    with pytest.raises(DeleteError):
        store.delete_job("abc")
