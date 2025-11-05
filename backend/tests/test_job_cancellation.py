from __future__ import annotations

from typing import Any, Dict, List, Optional

import pytest

from job_update.cancellation import CancellationError, InvalidCancellationState, JobCancellationService


class StubRepository:
    def __init__(self, item: Optional[Dict[str, Any]] = None) -> None:
        self.item = item or {
            "jobId": "abc",
            "status": "QUEUED",
        }
        self.updated_status: Optional[str] = None
        self.updated_attributes: Optional[Dict[str, Any]] = None

    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        if self.item and self.item.get("jobId") == job_id:
            return dict(self.item)
        return None

    def update_status(self, job_id: str, status: str, attributes: Dict[str, Any]) -> None:
        if not self.item or self.item.get("jobId") != job_id:
            raise CancellationError("missing job")
        self.updated_status = status
        self.updated_attributes = attributes
        self.item["status"] = status
        for key, value in attributes.items():
            if value is None and key in self.item:
                self.item.pop(key)
            else:
                self.item[key] = value


class StubPaginator:
    def __init__(self, executions: List[Dict[str, Any]]) -> None:
        self.executions = executions

    def paginate(self, **kwargs):  # pragma: no cover - simple iterator
        yield {"executions": self.executions}


class StubStepFunctions:
    class exceptions:  # pragma: no cover - structure for interface compatibility
        class ExecutionDoesNotExist(Exception):
            pass

    def __init__(self) -> None:
        self.stopped: List[str] = []
        self._executions: List[Dict[str, Any]] = []

    def stop_execution(self, *, executionArn: str, **_kwargs) -> None:
        if executionArn == "missing":
            raise StubStepFunctions.exceptions.ExecutionDoesNotExist()
        self.stopped.append(executionArn)

    def set_running_executions(self, executions: List[Dict[str, Any]]) -> None:
        self._executions = executions

    def get_paginator(self, _operation: str) -> StubPaginator:
        return StubPaginator(self._executions)


def test_cancel_queued_job_updates_status() -> None:
    repository = StubRepository()
    sfn = StubStepFunctions()
    service = JobCancellationService("jobs", "arn:aws:states:::statemachine", repository=repository, stepfunctions_client=sfn)

    result = service.cancel("abc")

    assert result["status"] == "CANCELED"
    assert repository.updated_status == "CANCELED"
    assert repository.updated_attributes == {
        "current_execution_arn": None,
        "error_message": "Canceled by user",
    }
    assert not sfn.stopped


def test_cancel_running_job_stops_execution() -> None:
    repository = StubRepository(
        {
            "jobId": "abc",
            "status": "RUNNING",
            "current_execution_arn": "arn:execution",
        }
    )
    sfn = StubStepFunctions()
    service = JobCancellationService("jobs", "arn:aws:states:::statemachine", repository=repository, stepfunctions_client=sfn)

    service.cancel("abc")

    assert "arn:execution" in sfn.stopped


def test_cancel_running_job_falls_back_to_search() -> None:
    repository = StubRepository(
        {
            "jobId": "abc",
            "status": "RUNNING",
        }
    )
    sfn = StubStepFunctions()
    sfn.set_running_executions([
        {"name": "xyz-123-abc", "executionArn": "arn:fallback"},
    ])
    service = JobCancellationService("jobs", "arn:aws:states:::statemachine", repository=repository, stepfunctions_client=sfn)

    service.cancel("abc")

    assert "arn:fallback" in sfn.stopped


def test_cancel_rejects_invalid_state() -> None:
    repository = StubRepository({"jobId": "abc", "status": "COMPLETED"})
    sfn = StubStepFunctions()
    service = JobCancellationService("jobs", "arn:aws:states:::statemachine", repository=repository, stepfunctions_client=sfn)

    with pytest.raises(InvalidCancellationState):
        service.cancel("abc")
