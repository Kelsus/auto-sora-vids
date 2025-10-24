from __future__ import annotations

import logging

from pathlib import Path

from aivideomaker.orchestrator import (
    ClipRenderResult,
    PipelineBundle,
    PipelineConfig,
    PipelineOrchestrator,
    PromptGenerationResult,
)
from aivideomaker.ssm import hydrate_env

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

    def run_prompts(self, article_url: str, dry_run: bool) -> PromptGenerationResult:
        orchestrator = self._ensure_orchestrator()
        self._data_root.mkdir(parents=True, exist_ok=True)
        result = orchestrator.generate_prompt_bundle(
            article_url=article_url,
            output_dir=self._data_root,
            dry_run=dry_run,
            cleanup=True,
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

        hydrate_env(config.anthropic_api_key_env, self._anthropic_api_key_parameter)
        hydrate_env("OPENAI_API_KEY", self._openai_api_key_parameter)
        hydrate_env(config.elevenlabs_api_key_env, self._elevenlabs_api_key_parameter)
        hydrate_env(config.veo_api_key_env, self._google_api_key_parameter)

        if updates:
            config = config.model_copy(update=updates)

        self._orchestrator = PipelineOrchestrator.default(config)
        return self._orchestrator
