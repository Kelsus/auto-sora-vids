from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Iterable, Mapping

import requests
import random
import re

from aivideomaker.prompt_builder.model import SoraPrompt

logger = logging.getLogger(__name__)


class SoraJobError(RuntimeError):
    """Raised when the Sora API reports a failure."""


class SoraClient:
    """Thin wrapper around the OpenAI Video API for Sora 2 clips."""

    def __init__(
        self,
        asset_dir: Path | None = None,
        api_key: str | None = None,
        model: str = "sora-2",
        size: str = "1280x720",
        poll_interval: float = 10.0,
        request_timeout: float = 30.0,
        max_wait: float = 600.0,
        submit_cooldown: float = 1.0,
    ) -> None:
        self._asset_dir = Path(asset_dir) if asset_dir is not None else None
        self.api_key = api_key
        self.model = model
        self.size = size
        self.poll_interval = poll_interval
        self.request_timeout = request_timeout
        self.max_wait = max_wait
        self.base_url = "https://api.openai.com/v1"
        self.submit_cooldown = max(0.0, submit_cooldown)
        self._last_submit_at = 0.0

    @property
    def asset_dir(self) -> Path:
        if self._asset_dir is None:
            raise RuntimeError("Sora asset directory is not configured")
        return self._asset_dir

    @asset_dir.setter
    def asset_dir(self, value: Path) -> None:
        self._asset_dir = Path(value)

    def _require_asset_dir(self) -> Path:
        if self._asset_dir is None:
            raise RuntimeError("Sora asset directory is not configured")
        self._asset_dir.mkdir(parents=True, exist_ok=True)
        return self._asset_dir

    def submit_prompts(self, prompts: Iterable[SoraPrompt], dry_run: bool = True) -> list[Path]:
        assets: list[Path] = []
        asset_dir = self._require_asset_dir()
        for prompt in prompts:
            target = asset_dir / f"{prompt.chunk_id}.mp4"
            if target.exists() and target.stat().st_size > 0:
                logger.info("Sora asset already exists for %s; skipping", prompt.chunk_id)
                assets.append(target)
                continue
            if dry_run or not self.api_key:
                logger.info("Sora dry run: skipping render for %s", prompt.chunk_id)
                target.touch()
                assets.append(target)
                continue
            logger.info("Submitting Sora job for %s", prompt.chunk_id)
            if target.exists() and target.stat().st_size == 0:
                target.unlink()
            self._respect_submit_cooldown()
            job = self._create_job_with_retry(prompt)
            job_id = job.get("id")
            if not job_id:
                raise SoraJobError(f"Sora create response missing job id: {json.dumps(job)}")
            try:
                job = self._poll_until_complete(job_id)
            except SoraJobError as exc:
                raise SoraJobError(f"Chunk {prompt.chunk_id} failed: {exc}") from exc
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
        desired = float(prompt.duration_sec or 8)
        for candidate in (4, 8, 12):
            if desired <= candidate:
                return candidate
        return 12

    def _compose_prompt(self, prompt: SoraPrompt, negative_prompt: str | None) -> str:
        parts = [
            prompt.visual_prompt,
            "Ensure visuals align with the voiceover narration without showing text or captions.",
            f"Audio direction: {prompt.audio_prompt}.",
        ]
        if negative_prompt:
            parts.append(f"Avoid: {negative_prompt}.")
        return "\n".join(parts)

    def _create_job_with_retry(self, prompt: SoraPrompt, retries: int = 3, backoff: float = 5.0) -> dict:
        last_error: Exception | None = None
        for attempt in range(1, retries + 1):
            try:
                return self._create_job(prompt)
            except requests.HTTPError as exc:  # pragma: no cover - network path
                status = exc.response.status_code if exc.response is not None else None
                if status and status >= 500 and attempt < retries:
                    logger.warning(
                        "Sora job create failed with %s; retrying in %ss (attempt %s/%s)",
                        status,
                        backoff,
                        attempt,
                        retries,
                    )
                    time.sleep(backoff)
                    last_error = exc
                    continue
                last_error = exc
                break
            except Exception as exc:  # pragma: no cover - network path
                last_error = exc
                break
        if last_error:
            raise last_error
        raise SoraJobError("Failed to create Sora job")

    def _create_job(self, prompt: SoraPrompt) -> dict:
        payload = {
            "model": self.model,
            "prompt": self._compose_prompt(prompt, prompt.negative_prompt),
            "seconds": str(self._safe_duration(prompt)),
            "size": self.size,
        }
        response = requests.post(
            f"{self.base_url}/videos",
            headers=self._headers(),
            json=payload,
            timeout=self.request_timeout,
        )
        if response.status_code >= 400:
            logger.error("Sora create job failed (%s): %s", response.status_code, response.text)
        response.raise_for_status()
        self._respect_rate_limits(response.headers)
        return response.json()

    def _poll_until_complete(self, job_id: str) -> dict:
        start = time.monotonic()
        status_payload: dict | None = None
        while True:
            if time.monotonic() - start > self.max_wait:
                raise TimeoutError(f"Sora job {job_id} timed out after {self.max_wait} seconds")
            try:
                response = requests.get(
                    f"{self.base_url}/videos/{job_id}",
                    headers=self._headers(),
                    timeout=self.request_timeout,
                )
                response.raise_for_status()
                status_payload = response.json()
                self._respect_rate_limits(response.headers)
            except (requests.exceptions.ReadTimeout, requests.exceptions.ConnectionError):  # pragma: no cover - network path
                logger.warning("Sora poll failed for %s; retrying", job_id)
                time.sleep(self.poll_interval)
                continue
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
        self._respect_rate_limits(response.headers)
        with target.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=8192):
                handle.write(chunk)
        logger.info("Saved Sora video to %s", target)

    def _respect_submit_cooldown(self) -> None:
        if self.submit_cooldown <= 0.0:
            return
        now = time.monotonic()
        remaining = self.submit_cooldown - (now - self._last_submit_at)
        if remaining > 0:
            time.sleep(remaining + random.uniform(0, 0.5))
        self._last_submit_at = time.monotonic()

    def _respect_rate_limits(self, headers: Mapping[str, str] | None) -> None:
        if not headers:
            return
        lower = {k.lower(): v for k, v in headers.items()}
        remaining = lower.get("x-ratelimit-remaining-requests")
        reset = lower.get("x-ratelimit-reset-requests")
        try:
            if remaining is not None and float(remaining) <= 0 and reset:
                sleep_seconds = self._parse_reset(reset)
                if sleep_seconds > 0:
                    jitter = random.uniform(0, 0.5)
                    logger.debug("Rate limit hit; sleeping %.2fs", sleep_seconds + jitter)
                    time.sleep(sleep_seconds + jitter)
        except ValueError:
            return

    @staticmethod
    def _parse_reset(value: str) -> float:
        if not value:
            return 0.0
        total = 0.0
        for amount, unit in re.findall(r"(\d+(?:\.\d+)?)([hms])", value):
            val = float(amount)
            if unit == "h":
                total += val * 3600
            elif unit == "m":
                total += val * 60
            else:
                total += val
        if total == 0.0:
            try:
                total = float(value)
            except ValueError:
                return 0.0
        return total
