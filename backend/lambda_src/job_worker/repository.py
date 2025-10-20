from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from common import JobsRepository
from job_worker.models import JobStatusUpdate


@dataclass
class JobRepository:
    table_name: str
    repository: JobsRepository | None = None

    def __post_init__(self) -> None:
        self._repository = self.repository or JobsRepository(self.table_name)

    def fetch(self, job_id: str) -> Optional[Dict[str, Any]]:
        return self._repository.get_job(job_id)

    def transition_to_running(self, job_id: str) -> bool:
        return self._repository.transition_status(job_id, "QUEUED", "RUNNING")

    def update_status(self, job_id: str, update: JobStatusUpdate) -> None:
        self._repository.update_status(job_id, update.status, update.attributes)
