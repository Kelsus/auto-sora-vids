from __future__ import annotations

from typing import Any, Dict, List

from common import JobsRepository, RepositoryError
from common.dynamodb_utils import normalize_dynamodb_value
from common.jobs_repository import JobNotFoundError
from common.time_utils import utc_now_iso, serialize_datetime
from job_update.models import JobUpdatePayload


class UpdateError(RuntimeError):
    """Raised when the update operation fails."""


class JobDoesNotExist(UpdateError):
    """Raised when attempting to update a job that does not exist."""


class JobUpdateStore:
    def __init__(self, table_name: str, repository: JobsRepository | None = None) -> None:
        self._repository = repository or JobsRepository(table_name)

    def update_job(self, job_id: str, payload: JobUpdatePayload) -> dict[str, Any]:
        attribute_names: Dict[str, str] = {"#updated_at": "updated_at"}
        attribute_values: Dict[str, Any] = {":updated_at": utc_now_iso()}
        set_parts: List[str] = ["#updated_at = :updated_at"]
        remove_parts: List[str] = []

        if payload.is_provided("status"):
            if payload.status is None:
                raise UpdateError("status cannot be null")
            attribute_names["#status"] = "status"
            attribute_values[":status"] = payload.status
            set_parts.append("#status = :status")

        if payload.is_provided("job_type"):
            if payload.job_type is None:
                raise UpdateError("job_type cannot be null")
            attribute_names["#job_type"] = "job_type"
            attribute_values[":job_type"] = payload.job_type
            set_parts.append("#job_type = :job_type")

        if payload.is_provided("scheduled_datetime"):
            if payload.scheduled_datetime is None:
                raise UpdateError("scheduled_datetime cannot be null")
            attribute_names["#scheduled_datetime"] = "scheduled_datetime"
            attribute_values[":scheduled_datetime"] = serialize_datetime(payload.scheduled_datetime)
            set_parts.append("#scheduled_datetime = :scheduled_datetime")

        if payload.is_provided("pipeline_config"):
            attribute_names["#metadata"] = "metadata"
            attribute_names["#pipeline_config"] = "pipeline_config"
            path = "#metadata.#pipeline_config"
            if payload.pipeline_config is None:
                remove_parts.append(path)
            else:
                attribute_values[":pipeline_config"] = payload.pipeline_config
                set_parts.append(f"{path} = :pipeline_config")

        try:
            self._repository.update_fields(
                job_id=job_id,
                set_parts=set_parts,
                attribute_names=attribute_names,
                attribute_values=attribute_values,
                remove_parts=remove_parts if remove_parts else None,
            )
        except JobNotFoundError as exc:
            raise JobDoesNotExist(str(exc)) from exc
        except RepositoryError as exc:
            raise UpdateError(str(exc)) from exc

        item = self._repository.get_job(job_id)
        if not item:
            raise JobDoesNotExist(f"Job '{job_id}' not found after update")
        return normalize_dynamodb_value(item)
