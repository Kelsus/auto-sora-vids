from __future__ import annotations

from typing import Any, Dict, List

from infra.lambda_src.job_scheduler.app import SchedulerApplication
from infra.lambda_src.job_scheduler.models import ScheduledJob
from infra.lambda_src.job_scheduler.settings import SchedulerSettings


class StubRepository:
    def __init__(self, items: List[Dict[str, Any]], consumable_ids: List[str]) -> None:
        self._items = items
        self._consumable_ids = set(consumable_ids)
        self.query_args = None

    def query_pending_before(self, index_name: str, scheduled_before_iso: str, limit: int):
        self.query_args = (index_name, scheduled_before_iso, limit)
        return self._items

    def transition_status(self, job_id: str, expected_status: str, new_status: str) -> bool:
        assert expected_status == "PENDING"
        assert new_status == "QUEUED"
        return job_id in self._consumable_ids


class RecordingExecutor:
    def __init__(self) -> None:
        self.started: List[ScheduledJob] = []

    def start_execution(self, job: ScheduledJob) -> None:
        self.started.append(job)


def test_scheduler_dispatches_only_available_jobs(monkeypatch):
    settings = SchedulerSettings(
        jobs_table_name="tbl",
        status_schedule_index="idx",
        state_machine_arn="arn:aws:states:us-east-1:123:stateMachine:demo",
        batch_size=10,
    )
    items = [
        {"jobId": "job-1", "url": "https://example.com/1"},
        {"jobId": "job-2", "url": "https://example.com/2"},
    ]
    repo = StubRepository(items=items, consumable_ids=["job-1"])
    executor = RecordingExecutor()
    app = SchedulerApplication(settings=settings, repository=repo, executor=executor)

    result = app.handle()

    assert result == {"evaluated": 2, "dispatched": 1}
    assert [job.job_id for job in executor.started] == ["job-1"]
    assert repo.query_args[0] == "idx"
    assert repo.query_args[2] == 10


def test_scheduler_handles_empty_query():
    settings = SchedulerSettings(
        jobs_table_name="tbl",
        status_schedule_index="idx",
        state_machine_arn="arn:aws:states:us-east-1:123:stateMachine:demo",
        batch_size=5,
    )
    repo = StubRepository(items=[], consumable_ids=[])
    executor = RecordingExecutor()
    app = SchedulerApplication(settings=settings, repository=repo, executor=executor)

    result = app.handle()

    assert result == {"evaluated": 0, "dispatched": 0}
    assert not executor.started
