from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Iterable

import requests

from aivideomaker.prompt_builder.model import SoraPrompt

logger = logging.getLogger(__name__)


class SoraJobError(RuntimeError):
    """Raised when the Sora API reports a failure."""


class SoraClient:
    """Thin wrapper around the OpenAI Video API for Sora 2 clips."""

    def __init__(
        self,
        asset_dir: Path,
        api_key: str | None = None,
        model: str = "sora-2",
        size: str = "1280x720",
        poll_interval: float = 10.0,
        request_timeout: float = 30.0,
        max_wait: float = 600.0,
    ) -> None:
        self.asset_dir = asset_dir
        self.asset_dir.mkdir(parents=True, exist_ok=True)
        self.api_key = api_key
        self.model = model
        self.size = size
        self.poll_interval = poll_interval
        self.request_timeout = request_timeout
        self.max_wait = max_wait
        self.base_url = "https://api.openai.com/v1"

    def submit_prompts(self, prompts: Iterable[SoraPrompt], dry_run: bool = True) -> list[Path]:
        assets: list[Path] = []
        for prompt in prompts:
            target = self.asset_dir / f"{prompt.chunk_id}.mp4"
            if dry_run or not self.api_key:
                logger.info("Sora dry run: skipping render for %s", prompt.chunk_id)
                target.touch()
                assets.append(target)
                continue
            logger.info("Submitting Sora job for %s", prompt.chunk_id)
            job = self._create_job(prompt)
            job_id = job.get("id")
            if not job_id:
                raise SoraJobError(f"Sora create response missing job id: {json.dumps(job)}")
            job = self._poll_until_complete(job_id)
            self._download_video(job_id, target)
            assets.append(target)
        return assets

    # Internal helpers -------------------------------------------------

    def _headers(self) -> dict[str, str]:
        if not self.api_key:
            raise RuntimeError("Sora API key not configured")
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _safe_duration(self, prompt: SoraPrompt) -> int:
        seconds = max(3, min(60, int(round(prompt.duration_sec or 10))))
        return seconds

    def _compose_prompt(self, prompt: SoraPrompt) -> str:
        return (
            f"{prompt.visual_prompt}\n"
            f"Ensure visuals support this narration: {prompt.transcript}\n"
            f"Audio direction: {prompt.audio_prompt}."
        )

    def _create_job(self, prompt: SoraPrompt) -> dict:
        payload = {
            "model": self.model,
            "prompt": self._compose_prompt(prompt),
            "seconds": str(self._safe_duration(prompt)),
            "size": self.size,
        }
        response = requests.post(
            f"{self.base_url}/videos",
            headers=self._headers(),
            json=payload,
            timeout=self.request_timeout,
        )
        response.raise_for_status()
        return response.json()

    def _poll_until_complete(self, job_id: str) -> dict:
        start = time.monotonic()
        status_payload: dict | None = None
        while True:
            if time.monotonic() - start > self.max_wait:
                raise TimeoutError(f"Sora job {job_id} timed out after {self.max_wait} seconds")
            response = requests.get(
                f"{self.base_url}/videos/{job_id}",
                headers=self._headers(),
                timeout=self.request_timeout,
            )
            response.raise_for_status()
            status_payload = response.json()
            status = status_payload.get("status")
            if status == "completed":
                logger.info("Sora job %s completed", job_id)
                return status_payload
            if status == "failed":
                error_message = status_payload.get("error") or status_payload
                raise SoraJobError(f"Sora job {job_id} failed: {error_message}")
            time.sleep(self.poll_interval)

    def _download_video(self, job_id: str, target: Path) -> None:
        response = requests.get(
            f"{self.base_url}/videos/{job_id}/content",
            headers={"Authorization": f"Bearer {self.api_key}"},
            stream=True,
            timeout=self.request_timeout,
            params={"variant": "video"},
        )
        response.raise_for_status()
        with target.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=8192):
                handle.write(chunk)
        logger.info("Saved Sora video to %s", target)
