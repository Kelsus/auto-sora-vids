from __future__ import annotations

import json
from dataclasses import dataclass

import boto3

from job_scheduler.models import ScheduledJob


@dataclass
class QueueDispatcher:
    queue_url: str

    def __post_init__(self) -> None:
        self._client = boto3.client("sqs")

    def dispatch(self, job: ScheduledJob) -> None:
        message_body = json.dumps(job.to_message())
        self._client.send_message(QueueUrl=self.queue_url, MessageBody=message_body)
