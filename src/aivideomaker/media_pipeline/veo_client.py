from __future__ import annotations

import logging
import time
import random
from collections import deque
from pathlib import Path
from typing import Deque, Iterable, Tuple

from google import genai
from google.genai import types

from aivideomaker.prompt_builder.model import SoraPrompt

logger = logging.getLogger(__name__)


class VeoJobError(RuntimeError):
    """Raised when the Veo API reports a failure."""


class VeoClient:
    """Client for Google's Veo 3 video generation API via the Gemini SDK."""

    def __init__(
        self,
        asset_dir: Path,
        api_key: str | None = None,
        model: str = "veo-3.0-generate-001",
        aspect_ratio: str = "16:9",
        poll_interval: float = 10.0,
        max_wait: float = 600.0,
        seed: int | None = None,
        max_concurrent_requests: int = 2,
        submit_cooldown: float = 0.0,
    ) -> None:
        self.asset_dir = asset_dir
        self.asset_dir.mkdir(parents=True, exist_ok=True)
        self.api_key = api_key
        self.model = model
        self.aspect_ratio = aspect_ratio
        self.poll_interval = poll_interval
        self.max_wait = max_wait
        self.client = genai.Client(api_key=api_key) if api_key else None
        self.seed = seed if seed is not None else random.randint(0, 2**31 - 1)
        self.max_concurrent_requests = max(1, max_concurrent_requests)
        self.submit_cooldown = max(0.0, submit_cooldown)
        self._last_submit_at = 0.0
        self._supports_seed = bool(getattr(getattr(self.client, "_api_client", None), "vertexai", False)) if self.client else False
        if self._supports_seed:
            logger.info("Initialized Veo client with seed %s", self.seed)
        else:
            logger.info("Initialized Veo client; seed %s will be ignored (not supported by current API)", self.seed)

    def submit_prompts(self, prompts: Iterable[SoraPrompt], dry_run: bool = True) -> list[Path]:
        assets: list[Path] = []
        pending: Deque[Tuple[SoraPrompt, types.GenerateVideosOperation, Path]] = deque()
        for prompt in prompts:
            target = self.asset_dir / f"{prompt.chunk_id}.mp4"
            if dry_run or not self.client:
                logger.info("Veo dry run: creating placeholder for %s", prompt.chunk_id)
                target.touch()
                assets.append(target)
                continue
            logger.info("Submitting Veo job for %s", prompt.chunk_id)
            operation = self._create_job(prompt)
            pending.append((prompt, operation, target))
            if len(pending) >= self.max_concurrent_requests:
                self._complete_next(pending, assets)
        while pending:
            self._complete_next(pending, assets)
        return assets

    # Internal helpers -------------------------------------------------

    def _safe_duration(self, prompt: SoraPrompt) -> int:
        # Veo currently produces up to 8-second clips; round down to stay within limits.
        return max(4, min(8, int(round(prompt.duration_sec or 8))))

    def _compose_prompt(self, prompt: SoraPrompt) -> str:
        segments = [prompt.visual_prompt]
        segments.append(f"Ensure visuals support this narration: {prompt.transcript}")
        segments.append(f"Audio direction: {prompt.audio_prompt}.")
        if prompt.negative_prompt:
            segments.append(f"Avoid: {prompt.negative_prompt}.")
        return "\n".join(segment for segment in segments if segment)

    def _create_job(self, prompt: SoraPrompt) -> types.GenerateVideosOperation:
        if not self.client:
            raise RuntimeError("Veo API client not configured")

        self._respect_submit_cooldown()
        duration = self._safe_duration(prompt)
        logger.info("Configured Veo duration %s seconds for chunk %s", duration, prompt.chunk_id)
        config_kwargs = dict(
            duration_seconds=duration,
            aspect_ratio=self.aspect_ratio,
            negative_prompt=prompt.negative_prompt,
        )
        if self._supports_seed:
            config_kwargs["seed"] = self.seed
            logger.debug("Using Veo seed %s for chunk %s", self.seed, prompt.chunk_id)
        config = types.GenerateVideosConfig(**config_kwargs)
        return self.client.models.generate_videos(
            model=self.model,
            prompt=self._compose_prompt(prompt),
            config=config,
        )

    def _respect_submit_cooldown(self) -> None:
        if self.submit_cooldown <= 0.0:
            return
        now = time.monotonic()
        remaining = self.submit_cooldown - (now - self._last_submit_at)
        if remaining > 0:
            logger.debug("Throttling Veo submission for %.2f seconds to respect API limits", remaining)
            time.sleep(remaining)
            now = time.monotonic()
        self._last_submit_at = now

    def _poll_until_complete(self, operation: types.GenerateVideosOperation) -> types.GenerateVideosResponse:
        if not self.client:
            raise RuntimeError("Veo API client not configured")

        start = time.monotonic()
        current = operation
        while not current.done:
            if time.monotonic() - start > self.max_wait:
                raise TimeoutError(f"Veo operation {current.name} timed out after {self.max_wait} seconds")
            time.sleep(self.poll_interval)
            current = self.client.operations.get(operation=current)

        if current.error is not None:
            raise VeoJobError(f"Veo job {current.name} failed: {current.error}")

        if not current.response:
            raise VeoJobError(f"Veo job {current.name} completed without a response payload")

        return current.response

    def _complete_next(
        self,
        pending: Deque[Tuple[SoraPrompt, types.GenerateVideosOperation, Path]],
        assets: list[Path],
    ) -> None:
        prompt, operation, target = pending.popleft()
        logger.debug("Awaiting Veo operation for %s", prompt.chunk_id)
        response = self._poll_until_complete(operation)
        self._save_video(response, target)
        assets.append(target)
        logger.info("Saved Veo clip for %s at %s", prompt.chunk_id, target)

    def _save_video(self, response: types.GenerateVideosResponse, target: Path) -> None:
        if not self.client:
            raise RuntimeError("Veo API client not configured")

        videos = getattr(response, "generated_videos", None) or []
        if not videos:
            raise VeoJobError("Veo response did not include generated videos")

        video_asset = getattr(videos[0], "video", None)
        if video_asset is None:
            raise VeoJobError("Veo response missing video asset data")

        self.client.files.download(file=video_asset)
        video_asset.save(str(target))
