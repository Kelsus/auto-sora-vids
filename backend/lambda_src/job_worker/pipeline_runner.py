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

logger = logging.getLogger(__name__)


class PipelineRunner:
    def __init__(self, data_root: Path, config_path: Path | None = None) -> None:
        self._data_root = data_root
        self._config_path = config_path
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
        if self._config_path:
            self._orchestrator = PipelineOrchestrator.from_file(self._config_path)
        else:
            self._orchestrator = PipelineOrchestrator.default(
                PipelineConfig(data_root=self._data_root)
            )
        return self._orchestrator
