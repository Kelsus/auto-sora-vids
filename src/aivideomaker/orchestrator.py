from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from pydantic import BaseModel

from aivideomaker.article_ingest.model import ArticleBundle
from aivideomaker.article_ingest.service import ArticleIngestor
from aivideomaker.chunker.model import ChunkPlan
from aivideomaker.chunker.planner import ChunkPlanner
from aivideomaker.prompt_builder.builder import PromptBuilder
from aivideomaker.prompt_builder.model import PromptBundle
from aivideomaker.script_engine.engine import ScriptEngine
from aivideomaker.script_engine.llm import ClaudeLLM, EchoLLM, LLMClient
from aivideomaker.script_engine.model import ScriptPlan
from aivideomaker.media_pipeline.sora_client import SoraClient
from aivideomaker.media_pipeline.voice import VoiceSessionManager
from aivideomaker.stitcher.assembler import Stitcher

logger = logging.getLogger(__name__)


class PipelineConfig(BaseModel):
    data_root: Path = Path("data")
    voice_id: str = "cameo_default"
    use_real_sora: bool = False
    llm_provider: str = "claude"
    llm_model: str = "claude-3-5-sonnet-20240620"
    anthropic_api_key_env: str = "ANTHROPIC_API_KEY"

    @classmethod
    def from_file(cls, path: Path) -> "PipelineConfig":
        text = path.read_text(encoding="utf-8")
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            import yaml  # type: ignore[import-not-found]

            payload = yaml.safe_load(text)
        return cls.model_validate(payload)

    def build_llm(self) -> LLMClient:
        provider = self.llm_provider.lower()
        if provider == "claude":
            try:
                from anthropic import Anthropic
            except ImportError as exc:  # pragma: no cover - guard for missing dependency
                raise RuntimeError("anthropic package is required for Claude integration") from exc

            api_key = os.getenv(self.anthropic_api_key_env)
            if not api_key:
                raise RuntimeError(
                    f"Missing Anthropics API key. Set {self.anthropic_api_key_env} in your environment."
                )
            client = Anthropic(api_key=api_key)
            return ClaudeLLM(client=client, model=self.llm_model)
        logger.warning("Unknown llm_provider '%s'; falling back to EchoLLM", provider)
        return EchoLLM()


class PipelineBundle(BaseModel):
    article: ArticleBundle
    script: ScriptPlan
    chunks: ChunkPlan
    prompts: PromptBundle
    sora_assets: list[Path]
    voice_transcript: Optional[Path]
    final_video: Optional[Path]


@dataclass
class PipelineOrchestrator:
    config: PipelineConfig
    article_ingestor: ArticleIngestor
    script_engine: ScriptEngine
    chunk_planner: ChunkPlanner
    prompt_builder: PromptBuilder
    sora_client: SoraClient
    voice_manager: VoiceSessionManager
    stitcher: Stitcher

    @classmethod
    def from_file(cls, path: Path) -> "PipelineOrchestrator":
        config = PipelineConfig.from_file(path)
        return cls.default(config)

    @classmethod
    def default(cls, config: PipelineConfig | None = None) -> "PipelineOrchestrator":
        config = config or PipelineConfig()
        data_root = config.data_root
        return cls(
            config=config,
            article_ingestor=ArticleIngestor(),
            script_engine=ScriptEngine(llm=config.build_llm()),
            chunk_planner=ChunkPlanner(),
            prompt_builder=PromptBuilder(default_voice=config.voice_id),
            sora_client=SoraClient(asset_dir=data_root / "media/sora_clips"),
            voice_manager=VoiceSessionManager(base_dir=data_root / "media/voice"),
            stitcher=Stitcher(export_dir=data_root / "exports"),
        )

    def run(self, article_url: str, output_dir: Path, dry_run: bool = True) -> PipelineBundle:
        logger.info("Ingesting article: %s", article_url)
        article = self.article_ingestor.ingest(article_url)

        logger.info("Generating suspenseful script")
        script = self.script_engine.generate_script(article)

        logger.info("Planning Sora-sized chunks")
        chunks = self.chunk_planner.plan(script)

        logger.info("Building structured prompts")
        prompts = self.prompt_builder.build(article, script, chunks)

        logger.info("Ensuring voice consistency")
        voice_asset = None
        if prompts.sora_prompts:
            directive = prompts.sora_prompts[0].cameo_voice
            if directive:
                voice_text = "\n\n".join(chunk.transcript for chunk in chunks.chunks)
                voice_asset = self.voice_manager.prepare_voice(directive, voice_text, dry_run=dry_run)
            else:
                logger.warning("No voice directive supplied; skipping voice preparation")

        logger.info("Submitting prompts to Sora (dry_run=%s)", dry_run)
        sora_assets = self.sora_client.submit_prompts(prompts.sora_prompts, dry_run=dry_run)

        final_video = None
        if not dry_run and sora_assets:
            final_video = self.stitcher.stitch(sora_assets, voice_asset)
        else:
            logger.info("Skipping stitching (dry run or no assets)")

        output_dir.mkdir(parents=True, exist_ok=True)
        return PipelineBundle(
            article=article,
            script=script,
            chunks=chunks,
            prompts=prompts,
            sora_assets=sora_assets,
            voice_transcript=voice_asset,
            final_video=final_video,
        )
