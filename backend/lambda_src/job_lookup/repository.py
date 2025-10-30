from __future__ import annotations

from decimal import Decimal
from typing import Any, Mapping

from common import JobsRepository, RepositoryError


class LookupError(RuntimeError):
    """Raised when the lookup Lambda cannot read the job record."""


class JobLookupStore:
    def __init__(self, table_name: str, repository: JobsRepository | None = None) -> None:
        self._repository = repository or JobsRepository(table_name)

    def get(self, job_id: str) -> dict[str, Any] | None:
        try:
            item = self._repository.get_job(job_id)
        except RepositoryError as exc:
            raise LookupError(str(exc)) from exc

        if item is None:
            return None

        return _normalize(item)


def _normalize(value: Any) -> Any:
    if isinstance(value, Decimal):
        if value == value.to_integral():
            return int(value)
        return float(value)
    if isinstance(value, Mapping):
        return {key: _normalize(val) for key, val in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalize(v) for v in value]
    if isinstance(value, set):
        return [_normalize(v) for v in value]
    return value
