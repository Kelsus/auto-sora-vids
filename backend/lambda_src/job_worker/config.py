from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class WorkerSettings:
    jobs_table_name: str
    output_bucket: str
    data_root: Path
    default_dry_run: bool
    final_video_prefix: str
    pipeline_config_path: Path | None
    veo_credentials_parameter: Optional[str] = None
    anthropic_api_key_parameter: Optional[str] = None
    openai_api_key_parameter: Optional[str] = None
    elevenlabs_api_key_parameter: Optional[str] = None
    google_api_key_parameter: Optional[str] = None

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
            veo_credentials_parameter=os.environ.get("VEO_CREDENTIALS_PARAMETER"),
            anthropic_api_key_parameter=os.environ.get("ANTHROPIC_API_KEY_PARAMETER"),
            openai_api_key_parameter=os.environ.get("OPENAI_API_KEY_PARAMETER"),
            elevenlabs_api_key_parameter=os.environ.get("ELEVEN_LABS_API_KEY_PARAMETER"),
            google_api_key_parameter=os.environ.get("GOOGLE_API_KEY_PARAMETER"),
        )

    def bundle_key(self, job_id: str) -> str:
        return f"jobs/{job_id}/bundle.json"

    def run_prefix(self, job_id: str) -> str:
        return f"jobs/{job_id}/run"

    def final_video_key(self, job_id: str, filename: str) -> str:
        return f"{self.final_video_prefix}/{job_id}-{filename}"
