from __future__ import annotations

from typing import Any, Dict, List
import sys
from pathlib import Path

root_dir = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root_dir / "backend" / "lambda_src"))
sys.path.insert(0, str(root_dir / "backend" / "lambda_src" / "common_layer" / "python"))

from job_scheduler.app import SchedulerApplication
from job_scheduler.models import ScheduledJob
from job_scheduler.settings import SchedulerSettings


class StubRepository:
    def __init__(
        self,
        scheduled_items: List[Dict[str, Any]],
        immediate_items: List[Dict[str, Any]],
        consumable_ids: List[str],
    ) -> None:
        self._scheduled_items = scheduled_items
        self._immediate_items = immediate_items
        self._consumable_ids = set(consumable_ids)
        self.query_args = None
        self.immediate_query_count = 0

    def query_pending_before(self, index_name: str, scheduled_before_iso: str, limit: int):
        self.query_args = (index_name, scheduled_before_iso, limit)
        return self._scheduled_items[:limit]

    def query_pending_immediate(self, index_name: str, limit: int):
        self.immediate_query_count += 1
        return self._immediate_items[:limit]

    def transition_status(self, job_id: str, expected_status: str, new_status: str) -> bool:
        assert expected_status == "PENDING"
        assert new_status == "QUEUED"
        return job_id in self._consumable_ids


class RecordingDispatcher:
    def __init__(self) -> None:
        self.started: List[ScheduledJob] = []

    def dispatch(self, job: ScheduledJob) -> None:
        self.started.append(job)


def test_scheduler_dispatches_only_available_jobs(monkeypatch):
    settings = SchedulerSettings(
        jobs_table_name="tbl",
        status_schedule_index="idx",
        dispatch_queue_url="https://sqs.us-east-1.amazonaws.com/123/demo",
        batch_size=10,
    )
    immediate_items = [
        {"jobId": "job-imm", "url": "https://example.com/immediate"},
    ]
    scheduled_items = [
        {"jobId": "job-1", "url": "https://example.com/1"},
        {"jobId": "job-2", "url": "https://example.com/2"},
    ]
    repo = StubRepository(
        scheduled_items=scheduled_items,
        immediate_items=immediate_items,
        consumable_ids=["job-imm", "job-1"],
    )
    dispatcher = RecordingDispatcher()
    app = SchedulerApplication(settings=settings, repository=repo, dispatcher=dispatcher)

    result = app.handle()

    assert result == {"evaluated": 3, "dispatched": 2}
    assert [job.job_id for job in dispatcher.started] == ["job-imm", "job-1"]
    assert repo.query_args[0] == "idx"
    assert repo.query_args[2] == settings.batch_size - len(immediate_items)


def test_scheduler_handles_empty_query():
    settings = SchedulerSettings(
        jobs_table_name="tbl",
        status_schedule_index="idx",
        dispatch_queue_url="https://sqs.us-east-1.amazonaws.com/123/demo",
        batch_size=5,
    )
    repo = StubRepository(scheduled_items=[], immediate_items=[], consumable_ids=[])
    dispatcher = RecordingDispatcher()
    app = SchedulerApplication(settings=settings, repository=repo, dispatcher=dispatcher)

    result = app.handle()

    assert result == {"evaluated": 0, "dispatched": 0}
    assert not dispatcher.started
