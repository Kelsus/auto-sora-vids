from __future__ import annotations

import logging
import random
import time
from collections import deque
from pathlib import Path
from typing import Deque, Iterable, Optional, Tuple
from urllib.parse import urlparse

from google import genai
from google.genai import types

try:
    from google.oauth2 import service_account
    import google.auth as google_auth
except ImportError:  # pragma: no cover - defensive guard in case auth libs are missing
    service_account = None  # type: ignore[assignment]
    google_auth = None  # type: ignore[assignment]

try:
    from google.cloud import storage
except ImportError:  # pragma: no cover - optional dependency for Vertex downloads
    storage = None  # type: ignore[assignment]

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
        use_vertex: bool = False,
        project: str | None = None,
        location: str | None = None,
        credentials_path: Path | None = None,
    ) -> None:
        self.asset_dir = asset_dir
        self.asset_dir.mkdir(parents=True, exist_ok=True)
        self.api_key = api_key
        self.model = model
        self.aspect_ratio = aspect_ratio
        self.poll_interval = poll_interval
        self.max_wait = max_wait
        self.seed = seed if seed is not None else random.randint(0, 2**31 - 1)
        self.max_concurrent_requests = max(1, max_concurrent_requests)
        self.submit_cooldown = max(0.0, submit_cooldown)
        self._last_submit_at = 0.0
        self.use_vertex = use_vertex
        self.project = project
        self.location = location or "us-central1"
        self.credentials_path = Path(credentials_path) if credentials_path else None
        self._credentials = None
        self.client = self._build_client()
        self._supports_seed = bool(getattr(getattr(self.client, "_api_client", None), "vertexai", False)) if self.client else False
        if self._supports_seed:
            logger.info("Initialized Veo client for Vertex AI project %s with seed %s", self.project, self.seed)
        else:
            logger.info("Initialized Veo client; seed %s will be ignored (Gemini API does not support seeds)", self.seed)

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
        # Veo supports 4, 6, or 8 second outputs; choose the smallest allowed duration
        # that can accommodate the requested length.
        approx = max(4.0, min(8.0, float(prompt.duration_sec or 8)))
        for candidate in (4, 6, 8):
            if approx <= candidate:
                return candidate
        return 8

    def _compose_prompt(self, prompt: SoraPrompt) -> str:
        segments = [prompt.visual_prompt]
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

        if self.use_vertex:
            self._save_vertex_video(video_asset, target)
        else:
            self.client.files.download(file=video_asset)
            video_asset.save(str(target))

    # Client helpers -------------------------------------------------

    def _build_client(self) -> Optional[genai.Client]:
        if self.use_vertex:
            return self._build_vertex_client()
        if self.api_key:
            logger.info("Using Gemini API key authentication for Veo")
            return genai.Client(api_key=self.api_key)
        logger.warning("No Veo API credentials provided; client will operate in dry-run mode only")
        return None

    def _build_vertex_client(self) -> genai.Client:
        if service_account is None:
            raise RuntimeError(
                "google.oauth2 is required for Vertex AI authentication; install google-auth."
            )
        credentials = None
        project = self.project

        if self.credentials_path and self.credentials_path.exists():
            logger.info("Loading Vertex credentials from %s", self.credentials_path)
            credentials = service_account.Credentials.from_service_account_file(
                str(self.credentials_path),
                scopes=["https://www.googleapis.com/auth/cloud-platform"],
            )
            project = project or getattr(credentials, "project_id", None)
        else:
            logger.info("Falling back to application default credentials for Vertex")
            if google_auth is None:
                raise RuntimeError(
                    "google.auth is required for Vertex AI authentication; install google-auth."
                )
            credentials, default_project = google_auth.default(
                scopes=["https://www.googleapis.com/auth/cloud-platform"]
            )
            project = project or default_project

        if not project:
            raise RuntimeError("Vertex AI configuration requires a project ID")

        self.project = project
        self._credentials = credentials
        logger.info("Initialized Vertex AI Veo client for project %s in %s", project, self.location)
        return genai.Client(
            vertexai=True,
            project=project,
            location=self.location,
            credentials=credentials,
        )

    def _save_vertex_video(self, video_asset: types.Video, target: Path) -> None:
        if video_asset is None:
            raise VeoJobError("Veo response missing video asset data")
        video_bytes = getattr(video_asset, "video_bytes", None)
        if video_bytes:
            target.write_bytes(video_bytes)
            return
        uri = getattr(video_asset, "uri", None)
        if not uri:
            raise VeoJobError("Vertex video asset missing downloadable URI")
        if uri.startswith("gs://"):
            self._download_gcs_uri(uri, target)
            return
        self._download_with_authorized_session(uri, target)

    def _download_gcs_uri(self, uri: str, target: Path) -> None:
        if storage is None:
            raise RuntimeError("google-cloud-storage is required to download Vertex assets")
        parsed = urlparse(uri)
        bucket_name = parsed.netloc
        blob_name = parsed.path.lstrip("/")
        if not bucket_name or not blob_name:
            raise VeoJobError(f"Malformed GCS URI: {uri}")
        client = storage.Client(project=self.project, credentials=self._credentials)
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(blob_name)
        blob.download_to_filename(str(target))
        logger.info("Downloaded Vertex video from %s", uri)

    def _download_with_authorized_session(self, uri: str, target: Path) -> None:
        if self._credentials is None:
            raise VeoJobError(f"Cannot download Veo asset from {uri}; missing credentials")
        from google.auth.transport.requests import AuthorizedSession

        session = AuthorizedSession(self._credentials)
        response = session.get(uri, stream=True, timeout=60)
        response.raise_for_status()
        with target.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=8192):
                handle.write(chunk)
        logger.info("Downloaded Vertex video via HTTPS from %s", uri)
