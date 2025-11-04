from __future__ import annotations

from decimal import Decimal
from typing import Any, Optional

import pytest

from common import RepositoryError
from job_list.app import JobListApplication
from job_list.repository import (
    InvalidCursor,
    JobListStore,
    ListError,
    ListResult,
    _encode_cursor,
)


class StubStore:
    def __init__(self, result: ListResult | None = None, error: Exception | None = None) -> None:
        self._result = result or ListResult(items=[], next_cursor=None)
        self._error = error
        self.args: dict[str, Any] | None = None

    def list_jobs(self, *, limit: int, cursor: str | None) -> ListResult:
        self.args = {"limit": limit, "cursor": cursor}
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
    assert store.args == {"limit": 10, "cursor": None}


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
    def __init__(self, items: list[dict[str, Any]], last_key: Optional[dict[str, Any]]) -> None:
        self._items = items
        self._last_key = last_key
        self.last_limit: Optional[int] = None
        self.last_cursor: Optional[dict[str, Any]] = None

    def list_jobs(
        self,
        limit: int,
        exclusive_start_key: Optional[dict[str, Any]] = None,
    ) -> tuple[list[dict[str, Any]], Optional[dict[str, Any]]]:
        self.last_limit = limit
        self.last_cursor = exclusive_start_key
        return self._items, self._last_key


def test_store_returns_encoded_cursor() -> None:
    repo = RecordingRepository(
        items=[{"jobId": "abc", "attempts": Decimal("2")}],
        last_key={"jobId": "def", "attempts": Decimal("3")},
    )
    store = JobListStore("jobs", repository=repo)  # type: ignore[arg-type]

    result = store.list_jobs(limit=5, cursor=None)

    assert repo.last_limit == 5
    assert repo.last_cursor is None
    assert result.items == [{"jobId": "abc", "attempts": 2}]
    assert result.next_cursor is not None

    repo_roundtrip = RecordingRepository(items=[], last_key=None)
    store_roundtrip = JobListStore("jobs", repository=repo_roundtrip)  # type: ignore[arg-type]
    store_roundtrip.list_jobs(limit=1, cursor=result.next_cursor)

    assert repo_roundtrip.last_cursor == {"jobId": "def", "attempts": Decimal("3")}


def test_store_raises_on_repository_error() -> None:
    class ErrorRepository(RecordingRepository):
        def list_jobs(
            self,
            limit: int,
            exclusive_start_key: Optional[dict[str, Any]] = None,
        ) -> tuple[list[dict[str, Any]], Optional[dict[str, Any]]]:
            raise RepositoryError("boom")

    store = JobListStore("jobs", repository=ErrorRepository([], None))  # type: ignore[arg-type]

    with pytest.raises(ListError):
        store.list_jobs(limit=10, cursor=None)


def test_encode_cursor_handles_none() -> None:
    assert _encode_cursor(None) is None
