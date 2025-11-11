from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping

from common import JobsRepository, RepositoryError, serialize_datetime


@dataclass(frozen=True)
class JobRecord:
    job_id: str
    url: str
    scheduled_datetime: datetime
    job_type: str
    status: str
    metadata: Mapping[str, Any]
    created_at: datetime
    updated_at: datetime

    def to_item(self) -> dict[str, Any]:
        created_iso = serialize_datetime(self.created_at)
        updated_iso = serialize_datetime(self.updated_at)
        return {
            "jobId": self.job_id,
            "pk2": "JOB",
            "url": self.url,
            "pk2": "JOB",
            "scheduled_datetime": serialize_datetime(self.scheduled_datetime),
            "job_type": self.job_type,
            "status": self.status,
            "metadata": dict(self.metadata),
            "createdAt": created_iso,
            "created_at": created_iso,
            "updatedAt": updated_iso,
            "updated_at": updated_iso,
        }


class JobStore:
    """Wraps the shared repository for ingest operations."""

    def __init__(self, table_name: str, repository: JobsRepository | None = None) -> None:
        self._repository = repository or JobsRepository(table_name)

    def save(self, record: JobRecord) -> None:
        try:
            self._repository.put_job(record.to_item())
        except RepositoryError as exc:
            raise PersistenceError(str(exc)) from exc


class PersistenceError(RuntimeError):
    """Raised when the ingest Lambda cannot persist the job record."""
