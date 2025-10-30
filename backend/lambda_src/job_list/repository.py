from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Dict, Optional, Tuple

from common import JobsRepository, RepositoryError
from common.dynamodb_utils import normalize_dynamodb_value


class ListError(RuntimeError):
    """Raised when the list Lambda cannot read from the repository."""


class InvalidCursor(ValueError):
    """Raised when a supplied cursor cannot be decoded."""


@dataclass
class ListResult:
    items: list[dict[str, Any]]
    next_cursor: str | None


class JobListStore:
    def __init__(self, table_name: str, repository: JobsRepository | None = None) -> None:
        self._repository = repository or JobsRepository(table_name)

    def list_jobs(self, limit: int, cursor: str | None) -> ListResult:
        exclusive_start_key = None
        if cursor:
            exclusive_start_key = _decode_cursor(cursor)

        try:
            items, last_key = self._repository.list_jobs(limit, exclusive_start_key)
        except RepositoryError as exc:
            raise ListError(str(exc)) from exc

        normalized_items = [normalize_dynamodb_value(item) for item in items]
        next_cursor = _encode_cursor(last_key) if last_key else None

        return ListResult(items=normalized_items, next_cursor=next_cursor)


def _encode_cursor(key: Optional[Dict[str, Any]]) -> str | None:
    if not key:
        return None
    normalized = normalize_dynamodb_value(key)
    raw = json.dumps(normalized).encode("utf-8")
    encoded = base64.urlsafe_b64encode(raw).decode("utf-8")
    return encoded


def _decode_cursor(cursor: str) -> Dict[str, Any]:
    try:
        padding = "=" * (-len(cursor) % 4)
        raw = base64.urlsafe_b64decode(cursor + padding)
    except (ValueError, base64.binascii.Error, TypeError) as exc:  # pragma: no cover - defensive
        raise InvalidCursor("Cursor is not valid base64") from exc

    try:
        data = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
        raise InvalidCursor("Cursor payload is invalid") from exc

    if not isinstance(data, dict):
        raise InvalidCursor("Cursor payload must be an object")

    return _restore_decimal(data)


def _restore_decimal(value: Any) -> Any:
    if isinstance(value, float) or isinstance(value, int):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {key: _restore_decimal(val) for key, val in value.items()}
    if isinstance(value, list):
        return [_restore_decimal(v) for v in value]
    return value
