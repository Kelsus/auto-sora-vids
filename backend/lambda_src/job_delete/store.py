from __future__ import annotations

from common import JobsRepository, RepositoryError
from common.jobs_repository import JobNotFoundError


class DeleteError(RuntimeError):
    """Raised when the delete Lambda cannot remove the job record."""


class JobDoesNotExist(DeleteError):
    """Raised when attempting to delete a job that does not exist."""


class JobDeleteStore:
    def __init__(self, table_name: str, repository: JobsRepository | None = None) -> None:
        self._repository = repository or JobsRepository(table_name)

    def delete_job(self, job_id: str) -> None:
        try:
            self._repository.delete_job(job_id)
        except JobNotFoundError as exc:
            raise JobDoesNotExist(str(exc)) from exc
        except RepositoryError as exc:
            raise DeleteError(str(exc)) from exc
