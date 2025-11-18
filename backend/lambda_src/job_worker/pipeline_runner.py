from __future__ import annotations

import logging
import os

from pathlib import Path

from aivideomaker.orchestrator import (
    ClipRenderResult,
    PipelineBundle,
    PipelineConfig,
    PipelineOrchestrator,
    PromptGenerationResult,
)
from aivideomaker.script_engine.reviewer import ScriptReviewDecision
from aivideomaker.ssm import get_parameter, hydrate_env

logger = logging.getLogger(__name__)


class PipelineRunner:
    def __init__(
        self,
        data_root: Path,
        base_config_path: Path | None = None,
        config_overrides: dict[str, object] | None = None,
        veo_credentials_parameter: str | None = None,
        anthropic_api_key_parameter: str | None = None,
        openai_api_key_parameter: str | None = None,
        elevenlabs_api_key_parameter: str | None = None,
        google_api_key_parameter: str | None = None,
    ) -> None:
        self._data_root = data_root
        self._base_config_path = base_config_path
        self._overrides = config_overrides or {}
        self._veo_credentials_parameter = veo_credentials_parameter
        self._anthropic_api_key_parameter = anthropic_api_key_parameter
        self._openai_api_key_parameter = openai_api_key_parameter
        self._elevenlabs_api_key_parameter = elevenlabs_api_key_parameter
        self._google_api_key_parameter = google_api_key_parameter
        self._orchestrator: PipelineOrchestrator | None = None

    def run_prompts(
        self,
        article_url: str,
        dry_run: bool,
        *,
        review_feedback: ScriptReviewDecision | None = None,
        article_override: dict[str, object] | None = None,
    ) -> PromptGenerationResult:
        orchestrator = self._ensure_orchestrator()
        self._data_root.mkdir(parents=True, exist_ok=True)
        result = orchestrator.generate_prompt_bundle(
            article_url=article_url,
            output_dir=self._data_root,
            dry_run=dry_run,
            cleanup=True,
            review_feedback=review_feedback,
            article_override=article_override,
        )
        logger.info("Generated prompts for %s", article_url)
        return result

    def render_clip(self, bundle: PipelineBundle, clip_id: str, dry_run: bool) -> ClipRenderResult:
        orchestrator = self._ensure_orchestrator()
        result = orchestrator.render_clip(
            bundle=bundle,
            clip_id=clip_id,
            output_dir=self._data_root,
            dry_run=dry_run,
        )
        logger.info("Rendered clip %s", clip_id)
        return result

    def stitch_final(self, bundle: PipelineBundle, dry_run: bool) -> PipelineBundle:
        orchestrator = self._ensure_orchestrator()
        result = orchestrator.stitch_bundle(
            bundle=bundle,
            output_dir=self._data_root,
            dry_run=dry_run,
        )
        logger.info("Stitched final video for %s", bundle.article.slug)
        return result

    def _ensure_orchestrator(self) -> PipelineOrchestrator:
        if self._orchestrator:
            return self._orchestrator
        if self._base_config_path:
            config = PipelineConfig.from_file(self._base_config_path)
        else:
            config = PipelineConfig(data_root=self._data_root)

        updates: dict[str, object] = {"data_root": self._data_root}
        updates.update(self._overrides)
        if self._veo_credentials_parameter and "veo_credentials_parameter" not in updates and not config.veo_credentials_parameter:
            updates["veo_credentials_parameter"] = self._veo_credentials_parameter

        credential_file = self._materialize_vertex_credentials()
        if credential_file and not config.veo_credentials_path and "veo_credentials_path" not in updates:
            updates["veo_credentials_path"] = credential_file

        hydrate_env(config.anthropic_api_key_env, self._anthropic_api_key_parameter)
        hydrate_env("OPENAI_API_KEY", self._openai_api_key_parameter)
        hydrate_env(config.elevenlabs_api_key_env, self._elevenlabs_api_key_parameter)
        hydrate_env(config.veo_api_key_env, self._google_api_key_parameter)

        if updates:
            config = config.model_copy(update=updates)

        self._orchestrator = PipelineOrchestrator.default(config)
        return self._orchestrator

    def _materialize_vertex_credentials(self) -> Path | None:
        if not self._veo_credentials_parameter:
            return None
        existing = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
        if existing:
            path = Path(existing)
            if path.exists():
                return path

        try:
            payload = get_parameter(self._veo_credentials_parameter)
        except Exception:
            logger.exception("Failed to load Vertex credentials from %s", self._veo_credentials_parameter)
            return None

        target = self._data_root / "veo-service-account.json"
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(payload, encoding="utf-8")
            os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(target)
            logger.info("Wrote Vertex credentials to %s", target)
            return target
        except Exception:
            logger.exception("Failed to materialize Vertex credentials at %s", target)
            return None
