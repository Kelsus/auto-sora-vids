from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List

import boto3


@dataclass
class ArtifactStorage:
    bucket: str

    def __post_init__(self) -> None:
        self._client = boto3.client("s3")

    def upload_directory(self, base_path: Path, prefix: str) -> List[str]:
        uploaded: List[str] = []
        for path in base_path.rglob("*"):
            if not path.is_file():
                continue
            relative = path.relative_to(base_path).as_posix()
            key = f"{prefix}/{relative}"
            self._client.upload_file(str(path), self.bucket, key)
            uploaded.append(key)
        return uploaded

    def upload_file(self, path: Path, key: str) -> None:
        self._client.upload_file(str(path), self.bucket, key)

    def download_prefix(self, prefix: str, target_dir: Path) -> None:
        target_dir.mkdir(parents=True, exist_ok=True)
        paginator = self._client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            for item in page.get("Contents", []):
                key = item["Key"]
                if key.endswith("/"):
                    continue
                relative = key[len(prefix) :].lstrip("/")
                local_path = target_dir / relative
                local_path.parent.mkdir(parents=True, exist_ok=True)
                self._client.download_file(self.bucket, key, str(local_path))
