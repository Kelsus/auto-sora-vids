from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class ForwarderSettings:
    secret_name: str
    folder_id: str

    @classmethod
    def from_env(cls) -> "ForwarderSettings":
        return cls(
            secret_name=os.environ["GDRIVE_SECRET_NAME"],
            folder_id=os.environ["GDRIVE_FOLDER_ID"],
        )
