from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import boto3
from botocore.exceptions import ClientError

import logging


logger = logging.getLogger(__name__)


@dataclass
class S3Object:
    bucket: str
    key: str
    body: bytes


@dataclass
class S3Reader:
    def __post_init__(self) -> None:
        self._client = boto3.client("s3")

    def fetch(self, bucket: str, key: str) -> Optional[S3Object]:
        try:
            response = self._client.get_object(Bucket=bucket, Key=key)
        except ClientError:
            logger.exception("Unable to fetch S3 object %s/%s", bucket, key)
            return None
        body = response["Body"].read()
        return S3Object(bucket=bucket, key=key, body=body)
