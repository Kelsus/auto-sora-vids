from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import boto3

from common import JobsRepository, RepositoryError
from common.dynamodb_utils import normalize_dynamodb_value
from job_update.store import JobDoesNotExist

logger = logging.getLogger(__name__)


class InvalidCancellationState(RuntimeError):
    """Raised when a job cannot be canceled from its current state."""


class CancellationError(RuntimeError):
    """Raised when the cancellation workflow fails to complete."""


class JobCancellationService:
    def __init__(
        self,
        table_name: str,
        state_machine_arn: str,
        repository: JobsRepository | None = None,
        stepfunctions_client: Any | None = None,
    ) -> None:
        self._repository = repository or JobsRepository(table_name)
        self._state_machine_arn = state_machine_arn
        self._sfn = stepfunctions_client or boto3.client("stepfunctions")

    def cancel(self, job_id: str) -> Dict[str, Any]:
        try:
            item = self._repository.get_job(job_id)
        except RepositoryError as exc:
            raise CancellationError(str(exc)) from exc

        if item is None:
            raise JobDoesNotExist(f"Job '{job_id}' not found")

        status = str(item.get("status", "")).upper()
        if status not in {"QUEUED", "RUNNING"}:
            raise InvalidCancellationState(f"Cannot cancel job in status '{status}'")

        execution_arn = item.get("current_execution_arn")
        if status == "RUNNING":
            target_arn = execution_arn or self._find_running_execution(job_id)
            if target_arn:
                self._stop_execution(target_arn)
            else:
                logger.warning("No running execution found for job %s", job_id)

        try:
            self._repository.update_status(
                job_id,
                "CANCELED",
                {
                    "current_execution_arn": None,
                    "error_message": "Canceled by user",
                },
            )
        except RepositoryError as exc:
            raise CancellationError(str(exc)) from exc

        updated = self._repository.get_job(job_id)
        if not updated:
            raise CancellationError(f"Job '{job_id}' missing after cancellation")
        return normalize_dynamodb_value(updated)

    def _stop_execution(self, execution_arn: str) -> None:
        try:
            self._sfn.stop_execution(
                executionArn=execution_arn,
                error="JobCanceled",
                cause="Job marked as CANCELED via API",
            )
        except self._sfn.exceptions.ExecutionDoesNotExist:  # type: ignore[attr-defined]
            logger.info("Execution %s already completed", execution_arn)

    def _find_running_execution(self, job_id: str) -> Optional[str]:
        paginator = self._sfn.get_paginator("list_executions")
        for page in paginator.paginate(
            stateMachineArn=self._state_machine_arn,
            statusFilter="RUNNING",
        ):
            for execution in page.get("executions", []):
                name = execution.get("name", "")
                if name.endswith(job_id) or job_id in name:
                    return execution.get("executionArn")
        return None
