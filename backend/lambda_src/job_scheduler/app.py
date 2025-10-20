from __future__ import annotations

import logging
from typing import Any, Dict, Iterable

from common import JobsRepository, utc_now_iso
from job_scheduler.executor import ExecutionLauncher
from job_scheduler.models import ScheduledJob
from job_scheduler.settings import SchedulerSettings

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class SchedulerApplication:
    def __init__(
        self,
        settings: SchedulerSettings | None = None,
        repository: JobsRepository | None = None,
        executor: ExecutionLauncher | None = None,
    ) -> None:
        self._settings = settings or SchedulerSettings.from_env()
        self._repository = repository or JobsRepository(self._settings.jobs_table_name)
        self._executor = executor or ExecutionLauncher(state_machine_arn=self._settings.state_machine_arn)

    def handle(self) -> Dict[str, Any]:
        scheduled_before = utc_now_iso()
        items = self._repository.query_pending_before(
            index_name=self._settings.status_schedule_index,
            scheduled_before_iso=scheduled_before,
            limit=self._settings.batch_size,
        )
        due_jobs: Iterable[ScheduledJob] = (ScheduledJob.from_item(item) for item in items)
        dispatched = 0
        evaluated = 0
        for job in due_jobs:
            evaluated += 1
            if self._repository.transition_status(job.job_id, "PENDING", "QUEUED"):
                self._executor.start_execution(job)
                dispatched += 1
                logger.info("Job %s dispatched to state machine", job.job_id)
        return {"evaluated": evaluated, "dispatched": dispatched}


def lambda_handler(_event: Dict[str, Any], _context: Any) -> Dict[str, Any]:  # pragma: no cover - AWS entry
    return SchedulerApplication().handle()
