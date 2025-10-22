from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class ForwarderSettings:
    service_account_parameter: str
    folder_id: str

    @classmethod
    def from_env(cls) -> "ForwarderSettings":
        return cls(
            service_account_parameter=os.environ["GDRIVE_SERVICE_ACCOUNT_PARAMETER"],
            folder_id=os.environ["GDRIVE_FOLDER_ID"],
        )
