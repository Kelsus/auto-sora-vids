from __future__ import annotations

from typing import Any, Dict

from job_delete.app import JobDeleteApplication


def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:  # pragma: no cover - AWS entry
    return JobDeleteApplication().handle_event(event)

