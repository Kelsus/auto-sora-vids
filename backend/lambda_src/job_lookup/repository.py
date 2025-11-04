from __future__ import annotations

from typing import Any

from common import JobsRepository, RepositoryError
from common.dynamodb_utils import normalize_dynamodb_value


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

        return normalize_dynamodb_value(item)
