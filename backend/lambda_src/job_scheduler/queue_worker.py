from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List

from job_scheduler.executor import ExecutionLauncher
from job_scheduler.models import ScheduledJob

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

_LAUNCHER: ExecutionLauncher | None = None


def _get_launcher() -> ExecutionLauncher:
    global _LAUNCHER
    if _LAUNCHER is None:
        state_machine_arn = os.environ["STATE_MACHINE_ARN"]
        _LAUNCHER = ExecutionLauncher(state_machine_arn=state_machine_arn)
    return _LAUNCHER


def handler(event: Dict[str, Any], _context: Any) -> Dict[str, Any]:
    failures: List[Dict[str, str]] = []
    launcher = _get_launcher()
    for record in event.get("Records", []):
        message_id = record.get("messageId", "unknown")
        try:
            body = json.loads(record.get("body", "{}"))
            job = ScheduledJob.from_item(body)
            launcher.start_execution(job)
        except Exception:  # pragma: no cover - defensive logging
            logger.exception("Failed to launch Step Functions execution for message %s", message_id)
            failures.append({"itemIdentifier": message_id})
    if failures:
        return {"batchItemFailures": failures}
    return {}
