from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class ForwarderSettings:
    videopusher_table_name: str
    videopusher_bucket_name: str
    source_bucket_name: str | None = None

    @classmethod
    def from_env(cls) -> ForwarderSettings:
        return cls(
            videopusher_table_name=os.environ["VIDEOPUSHER_TABLE_NAME"],
            videopusher_bucket_name=os.environ["VIDEOPUSHER_BUCKET_NAME"],
            source_bucket_name=os.environ.get("SOURCE_BUCKET_NAME"),
        )
