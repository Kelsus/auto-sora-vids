from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class WorkerSettings:
    jobs_table_name: str
    output_bucket: str
    data_root: Path
    default_dry_run: bool
    final_video_prefix: str
    pipeline_config_path: Path | None

    @classmethod
    def from_env(cls) -> "WorkerSettings":
        return cls(
            jobs_table_name=os.environ["JOBS_TABLE_NAME"],
            output_bucket=os.environ["OUTPUT_BUCKET"],
            data_root=Path(os.environ.get("DATA_ROOT", "/tmp/data")),
            default_dry_run=os.environ.get("DEFAULT_DRY_RUN", "false").lower() == "true",
            final_video_prefix=os.environ.get("FINAL_VIDEO_PREFIX", "jobs/final"),
            pipeline_config_path=Path(os.environ["PIPELINE_CONFIG_PATH"])
            if "PIPELINE_CONFIG_PATH" in os.environ
            else None,
        )

    def bundle_key(self, job_id: str) -> str:
        return f"jobs/{job_id}/bundle.json"

    def run_prefix(self, job_id: str) -> str:
        return f"jobs/{job_id}/run"

    def final_video_key(self, job_id: str, filename: str) -> str:
        return f"{self.final_video_prefix}/{job_id}-{filename}"
