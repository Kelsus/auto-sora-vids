"""Job worker Lambda package."""

from __future__ import annotations

from typing import Any, Dict

def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """Lazily import the real handler to avoid heavy deps at package import time."""
    from job_worker.handler import handler as _handler

    return _handler(event, context)

__all__ = ["lambda_handler"]
