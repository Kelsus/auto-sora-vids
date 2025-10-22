from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass

import boto3

from job_scheduler.models import ScheduledJob


@dataclass
class ExecutionLauncher:
    state_machine_arn: str

    def __post_init__(self) -> None:
        self._client = boto3.client("stepfunctions")

    def start_execution(self, job: ScheduledJob) -> None:
        execution_name = self._execution_name(job.job_id)
        payload = {
            "jobId": job.job_id,
            "articleUrl": job.url,
            "socialMedia": job.social_media,
            "scheduledDatetime": job.scheduled_datetime,
            "metadata": job.metadata,
            "jobType": job.job_type,
        }
        self._client.start_execution(
            stateMachineArn=self.state_machine_arn,
            name=execution_name,
            input=json.dumps(payload),
        )

    def _execution_name(self, job_id: str) -> str:
        suffix = uuid.uuid4().hex[:8]
        timestamp = int(time.time())
        base = f"{suffix}-{timestamp}-{job_id}"
        return base[:80]
