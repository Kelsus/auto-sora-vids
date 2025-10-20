from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class SchedulerSettings:
    jobs_table_name: str
    status_schedule_index: str
    state_machine_arn: str
    batch_size: int = 25

    @classmethod
    def from_env(cls) -> "SchedulerSettings":
        return cls(
            jobs_table_name=os.environ["JOBS_TABLE_NAME"],
            status_schedule_index=os.environ["STATUS_SCHEDULE_INDEX"],
            state_machine_arn=os.environ["STATE_MACHINE_ARN"],
            batch_size=int(os.environ.get("BATCH_SIZE", "25")),
        )
