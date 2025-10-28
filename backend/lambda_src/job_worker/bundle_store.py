from __future__ import annotations

import json
from dataclasses import dataclass

import boto3

from aivideomaker.orchestrator import PipelineBundle


@dataclass
class BundleStore:
    bucket: str

    def __post_init__(self) -> None:
        self._client = boto3.client("s3")

    def load(self, key: str) -> PipelineBundle:
        response = self._client.get_object(Bucket=self.bucket, Key=key)
        payload = json.loads(response["Body"].read().decode("utf-8"))
        return PipelineBundle.model_validate(payload)

    def save(self, key: str, bundle: PipelineBundle) -> None:
        payload = json.dumps(bundle.model_dump(mode="json"), indent=2).encode("utf-8")
        self._client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=payload,
            ContentType="application/json",
        )
