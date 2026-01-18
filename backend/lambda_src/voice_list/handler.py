from __future__ import annotations
# v1/shared-voices with language=en filter - 2026-01-18
from typing import Any, Dict

from voice_list.app import lambda_handler


def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:  # pragma: no cover - AWS entry
    return lambda_handler(event, context)
