from __future__ import annotations

from typing import Any, Optional

import pytest

from common import RepositoryError
from job_list.app import DEFAULT_LIMIT, JobListApplication
from job_list.repository import InvalidCursor, JobListStore, ListError, ListResult, _encode_cursor


class StubStore:
    def __init__(self, result: ListResult | None = None, error: Exception | None = None) -> None:
        self._result = result or ListResult(items=[], next_cursor=None)
        self._error = error
        self.args: dict[str, Any] | None = None

    def list_jobs(self, *, limit: int, cursor: str | None, status: str | None) -> ListResult:
        self.args = {"limit": limit, "cursor": cursor, "status": status}
        if self._error:
            raise self._error
        return self._result


def _build_event(method: str = "GET", params: dict[str, str] | None = None) -> dict[str, Any]:
    return {
        "httpMethod": method,
        "queryStringParameters": params,
    }


def test_returns_paginated_jobs(monkeypatch: pytest.MonkeyPatch) -> None:
    result = ListResult(
        items=[{"jobId": "a"}, {"jobId": "b"}],
        next_cursor="next",
    )
    store = StubStore(result=result)
    monkeypatch.setenv("JOBS_TABLE_NAME", "jobs")
    app = JobListApplication(store=store)

    response = app.handle_event(_build_event(params={"limit": "10"}))

    assert response["statusCode"] == 200
    assert response["body"] == '{"items": [{"jobId": "a"}, {"jobId": "b"}], "nextCursor": "next"}'
    assert store.args == {"limit": 10, "cursor": None, "status": None}


def test_accepts_status_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    result = ListResult(items=[], next_cursor=None)
    store = StubStore(result=result)
    monkeypatch.setenv("JOBS_TABLE_NAME", "jobs")
    app = JobListApplication(store=store)

    response = app.handle_event(_build_event(params={"status": "completed"}))

    assert response["statusCode"] == 200
    assert store.args == {"limit": DEFAULT_LIMIT, "cursor": None, "status": "COMPLETED"}


def test_returns_bad_request_for_invalid_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    store = StubStore()
    monkeypatch.setenv("JOBS_TABLE_NAME", "jobs")
    app = JobListApplication(store=store)

    response = app.handle_event(_build_event(params={"limit": "0"}))

    assert response["statusCode"] == 400


def test_returns_bad_request_for_invalid_cursor(monkeypatch: pytest.MonkeyPatch) -> None:
    store = StubStore(error=InvalidCursor("bad cursor"))
    monkeypatch.setenv("JOBS_TABLE_NAME", "jobs")
    app = JobListApplication(store=store)

    response = app.handle_event(_build_event(params={"cursor": "!!!"}))

    assert response["statusCode"] == 400


def test_returns_server_error_on_store_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    store = StubStore(error=ListError("boom"))
    monkeypatch.setenv("JOBS_TABLE_NAME", "jobs")
    app = JobListApplication(store=store)

    response = app.handle_event(_build_event())

    assert response["statusCode"] == 500


def test_handles_preflight(monkeypatch: pytest.MonkeyPatch) -> None:
    store = StubStore()
    monkeypatch.setenv("JOBS_TABLE_NAME", "jobs")
    app = JobListApplication(store=store)

    response = app.handle_event(_build_event(method="OPTIONS"))

    assert response["statusCode"] == 204


class RecordingRepository:
    def __init__(
        self,
        responses: list[tuple[list[dict[str, Any]], Optional[dict[str, Any]]]],
        status_responses: Optional[list[tuple[list[dict[str, Any]], Optional[dict[str, Any]]]]] = None,
    ) -> None:
        self._responses = responses
        self._status_responses = status_responses or []
        self.calls: list[dict[str, Any]] = []
        self.status_calls: list[dict[str, Any]] = []

    def list_jobs(
        self,
        limit: int,
        exclusive_start_key: Optional[dict[str, Any]] = None,
    ) -> tuple[list[dict[str, Any]], Optional[dict[str, Any]]]:
        self.calls.append({"limit": limit, "cursor": exclusive_start_key})
        if not self._responses:
            return [], None
        return self._responses.pop(0)

    def list_jobs_by_status(
        self,
        status: str,
        limit: int,
        exclusive_start_key: Optional[dict[str, Any]] = None,
    ) -> tuple[list[dict[str, Any]], Optional[dict[str, Any]]]:
        self.status_calls.append({"status": status, "limit": limit, "cursor": exclusive_start_key})
        if not self._status_responses:
            return [], None
        return self._status_responses.pop(0)


def test_store_returns_encoded_cursor() -> None:
    repo = RecordingRepository(
        responses=[
            (
                [
                    {"jobId": "abc", "created_at": "2025-01-03T08:00:00+00:00", "pk2": "JOB"},
                    {"jobId": "def", "created_at": "2025-01-02T10:00:00+00:00", "pk2": "JOB"},
                ],
                {"jobId": "def", "created_at": "2025-01-02T10:00:00+00:00", "pk2": "JOB"},
            ),
            (
                [{"jobId": "ghi", "created_at": "2025-01-01T09:00:00+00:00", "pk2": "JOB"}],
                None,
            ),
        ]
    )
    store = JobListStore("jobs", repository=repo)  # type: ignore[arg-type]

    result = store.list_jobs(limit=5, cursor=None, status=None)

    assert [item["jobId"] for item in result.items] == ["abc", "def"]
    assert result.next_cursor is not None

    next_result = store.list_jobs(limit=5, cursor=result.next_cursor, status=None)

    assert [item["jobId"] for item in next_result.items] == ["ghi"]
    assert next_result.next_cursor is None
    assert repo.calls[1]["cursor"] == {"jobId": "def", "created_at": "2025-01-02T10:00:00+00:00", "pk2": "JOB"}


def test_store_raises_on_repository_error() -> None:
    class ErrorRepository:
        def list_jobs(
            self,
            limit: int,
            exclusive_start_key: Optional[dict[str, Any]] = None,
        ) -> tuple[list[dict[str, Any]], Optional[dict[str, Any]]]:
            raise RepositoryError("boom")

    store = JobListStore("jobs", repository=ErrorRepository())  # type: ignore[arg-type]

    with pytest.raises(ListError):
        store.list_jobs(limit=10, cursor=None, status=None)


def test_store_uses_status_index_when_filter_provided() -> None:
    repo = RecordingRepository(
        responses=[],
        status_responses=[
            (
                [
                    {"jobId": "xyz", "status": "FAILED", "created_at": "2025-01-04T12:00:00+00:00"},
                    {"jobId": "uvw", "status": "FAILED", "created_at": "2025-01-03T08:00:00+00:00"},
                ],
                {"jobId": "uvw", "status": "FAILED", "created_at": "2025-01-03T08:00:00+00:00"},
            )
        ],
    )
    store = JobListStore("jobs", repository=repo)  # type: ignore[arg-type]

    result = store.list_jobs(limit=10, cursor=None, status="FAILED")

    assert repo.calls == []
    assert len(repo.status_calls) == 1
    assert repo.status_calls[0]["status"] == "FAILED"
    assert [item["jobId"] for item in result.items] == ["xyz", "uvw"]
def test_store_rejects_cursor_mismatch_for_status_filter() -> None:
    repo = RecordingRepository(
        responses=[],
        status_responses=[
            (
                [{"jobId": "a", "status": "FAILED", "created_at": "2025-01-02T10:00:00+00:00"}],
                None,
            )
        ],
    )
    store = JobListStore("jobs", repository=repo)  # type: ignore[arg-type]

    cursor = _encode_cursor({"pk2": "JOB", "jobId": "abc", "created_at": "2025-01-02T10:00:00+00:00"})

    with pytest.raises(InvalidCursor):
        store.list_jobs(limit=5, cursor=cursor, status="FAILED")


def test_store_rejects_cursor_without_status_when_filtering() -> None:
    repo = RecordingRepository(
        responses=[],
        status_responses=[
            (
                [{"jobId": "a", "status": "FAILED", "created_at": "2025-01-02T10:00:00+00:00"}],
                None,
            )
        ],
    )
    store = JobListStore("jobs", repository=repo)  # type: ignore[arg-type]

    cursor_payload = {"jobId": "abc", "created_at": "2025-01-02T10:00:00+00:00"}
    cursor = _encode_cursor(cursor_payload)

    with pytest.raises(InvalidCursor):
        store.list_jobs(limit=5, cursor=cursor, status="FAILED")
