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
