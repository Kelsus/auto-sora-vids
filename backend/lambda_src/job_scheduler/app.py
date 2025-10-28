from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, List

from common import JobsRepository, utc_now_iso
from job_scheduler.dispatcher import QueueDispatcher
from job_scheduler.models import ScheduledJob
from job_scheduler.settings import SchedulerSettings

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class SchedulerApplication:
    def __init__(
        self,
        settings: SchedulerSettings | None = None,
        repository: JobsRepository | None = None,
        dispatcher: QueueDispatcher | None = None,
    ) -> None:
        self._settings = settings or SchedulerSettings.from_env()
        self._repository = repository or JobsRepository(self._settings.jobs_table_name)
        self._dispatcher = dispatcher or QueueDispatcher(queue_url=self._settings.dispatch_queue_url)

    def handle(self) -> Dict[str, Any]:
        scheduled_before = utc_now_iso()
        immediate_items = self._repository.query_pending_immediate(
            index_name=self._settings.status_schedule_index,
            limit=self._settings.batch_size,
        )
        remaining = max(self._settings.batch_size - len(immediate_items), 0)
        scheduled_items: List[Dict[str, Any]] = []
        if remaining > 0:
            scheduled_items = self._repository.query_pending_before(
                index_name=self._settings.status_schedule_index,
                scheduled_before_iso=scheduled_before,
                limit=remaining,
            )
        combined_items: List[Dict[str, Any]] = []
        seen_ids: set[str] = set()
        for item in immediate_items + scheduled_items:
            job_id = item.get("jobId")
            if job_id in seen_ids:
                continue
            seen_ids.add(job_id)
            combined_items.append(item)
        items = combined_items
        dispatched = 0
        evaluated = 0
        for item in items:
            evaluated += 1
            if "url" not in item:
                logger.warning("Skipping job %s without url attribute", item.get("jobId", "unknown"))
                continue
            job = ScheduledJob.from_item(item)
            if self._repository.transition_status(job.job_id, "PENDING", "QUEUED"):
                self._dispatcher.dispatch(job)
                dispatched += 1
                logger.info("Job %s dispatched to state machine", job.job_id)
        return {"evaluated": evaluated, "dispatched": dispatched}


def lambda_handler(_event: Dict[str, Any], _context: Any) -> Dict[str, Any]:  # pragma: no cover - AWS entry
    return SchedulerApplication().handle()
