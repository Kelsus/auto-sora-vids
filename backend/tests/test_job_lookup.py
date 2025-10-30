from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, Optional

import pytest

from common import RepositoryError
from job_lookup.app import JobLookupApplication
from job_lookup.repository import JobLookupStore, LookupError


class StubLookupStore:
    def __init__(self, result: Optional[dict[str, Any]] = None, error: bool = False) -> None:
        self.result = result
        self.error = error
        self.requested_job_id: Optional[str] = None

    def get(self, job_id: str) -> Optional[dict[str, Any]]:
        self.requested_job_id = job_id
        if self.error:
            raise LookupError("boom")
        return self.result


def _build_event(job_id: str | None, method: str = "GET") -> Dict[str, Any]:
    path_params = {"jobId": job_id} if job_id is not None else None
    return {
        "httpMethod": method,
        "pathParameters": path_params,
    }


def test_returns_job_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    store = StubLookupStore(result={"jobId": "abc", "status": "PENDING"})
    monkeypatch.setenv("JOBS_TABLE_NAME", "jobs")
    app = JobLookupApplication(repository=store)

    response = app.handle_event(_build_event(" abc "))

    assert response["statusCode"] == 200
    assert response["body"] == '{"jobId": "abc", "status": "PENDING"}'
    assert store.requested_job_id == "abc"


def test_returns_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    store = StubLookupStore(result=None)
    monkeypatch.setenv("JOBS_TABLE_NAME", "jobs")
    app = JobLookupApplication(repository=store)

    response = app.handle_event(_build_event("missing"))

    assert response["statusCode"] == 404
    assert "not found" in response["body"]


def test_returns_bad_request_when_job_id_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    store = StubLookupStore(result=None)
    monkeypatch.setenv("JOBS_TABLE_NAME", "jobs")
    app = JobLookupApplication(repository=store)

    response = app.handle_event(_build_event(None))

    assert response["statusCode"] == 400


def test_returns_server_error_on_repository_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    store = StubLookupStore(error=True)
    monkeypatch.setenv("JOBS_TABLE_NAME", "jobs")
    app = JobLookupApplication(repository=store)

    response = app.handle_event(_build_event("abc"))

    assert response["statusCode"] == 500


def test_allows_cors_preflight(monkeypatch: pytest.MonkeyPatch) -> None:
    store = StubLookupStore(result=None)
    monkeypatch.setenv("JOBS_TABLE_NAME", "jobs")
    app = JobLookupApplication(repository=store)

    response = app.handle_event(_build_event("ignored", method="OPTIONS"))

    assert response["statusCode"] == 204


class FakeJobsRepository:
    def __init__(self, item: Optional[dict[str, Any]]) -> None:
        self._item = item

    def get_job(self, job_id: str) -> Optional[dict[str, Any]]:
        return self._item


def test_store_normalizes_decimal_values() -> None:
    repo = FakeJobsRepository(
        {
            "jobId": "abc",
            "attempts": Decimal("2"),
            "score": Decimal("1.5"),
            "tags": [Decimal("3"), {"nested": Decimal("4")}],
            "flags": {Decimal("5"), Decimal("6")},
        }
    )
    store = JobLookupStore("jobs", repository=repo)  # type: ignore[arg-type]

    item = store.get("abc")

    assert item is not None
    assert item["jobId"] == "abc"
    assert item["attempts"] == 2
    assert item["score"] == 1.5
    assert item["tags"] == [3, {"nested": 4}]
    assert sorted(item["flags"]) == [5, 6]


def test_store_propagates_repository_errors() -> None:
    class ErrorRepo(FakeJobsRepository):
        def get_job(self, job_id: str) -> Optional[dict[str, Any]]:
            raise RepositoryError("nope")

    store = JobLookupStore("jobs", repository=ErrorRepo(None))  # type: ignore[arg-type]

    with pytest.raises(LookupError):
        store.get("abc")
