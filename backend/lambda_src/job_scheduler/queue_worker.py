from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List

from common import JobsRepository
from job_scheduler.executor import ExecutionLauncher
from job_scheduler.models import ScheduledJob

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

_LAUNCHER: ExecutionLauncher | None = None
_REPOSITORY: JobsRepository | None = None


def _get_launcher() -> ExecutionLauncher:
    global _LAUNCHER
    if _LAUNCHER is None:
        state_machine_arn = os.environ["STATE_MACHINE_ARN"]
        _LAUNCHER = ExecutionLauncher(state_machine_arn=state_machine_arn)
    return _LAUNCHER


def _get_repository() -> JobsRepository:
    global _REPOSITORY
    if _REPOSITORY is None:
        table_name = os.environ["JOBS_TABLE_NAME"]
        _REPOSITORY = JobsRepository(table_name)
    return _REPOSITORY


def handler(event: Dict[str, Any], _context: Any) -> Dict[str, Any]:
    failures: List[Dict[str, str]] = []
    launcher = _get_launcher()
    repository = _get_repository()
    for record in event.get("Records", []):
        message_id = record.get("messageId", "unknown")
        try:
            body = json.loads(record.get("body", "{}"))
            job = ScheduledJob.from_item(body)
            item = repository.get_job(job.job_id)
            if not item:
                logger.warning("Job %s not found; skipping execution", job.job_id)
                continue
            status = str(item.get("status", "")).upper()
            if status == "CANCELED":
                logger.info("Job %s is CANCELED; skipping execution launch", job.job_id)
                continue
            execution_arn = launcher.start_execution(job)
            repository.update_status(
                job.job_id,
                "QUEUED",
                {"current_execution_arn": execution_arn},
            )
        except Exception:  # pragma: no cover - defensive logging
            logger.exception("Failed to launch Step Functions execution for message %s", message_id)
            failures.append({"itemIdentifier": message_id})
    if failures:
        return {"batchItemFailures": failures}
    return {}
