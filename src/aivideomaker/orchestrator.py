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
from aivideomaker.media_pipeline.veo_client import VeoClient
from aivideomaker.media_pipeline.voice import VoiceSessionManager
from aivideomaker.stitcher.assembler import Stitcher

logger = logging.getLogger(__name__)


class PipelineConfig(BaseModel):
    data_root: Path = Path("data")
    voice_id: Optional[str] = None
    llm_provider: str = "claude"
    llm_model: str = "claude-sonnet-4-5"
    anthropic_api_key_env: str = "ANTHROPIC_API_KEY"
    media_provider: str = "veo"
    negative_prompt: Optional[str] = None
    # Sora configuration
    use_real_sora: bool = False
    sora_model: str = "sora-2"
    sora_size: str = "1280x720"
    sora_api_key_env: str = "OPENAI_API_KEY"
    sora_poll_interval: float = 10.0
    sora_request_timeout: float = 30.0
    sora_max_wait: float = 600.0
    # Veo configuration
    veo_model: str = "veo-3.0-generate-001"
    veo_api_key_env: str = "GOOGLE_API_KEY"
    veo_aspect_ratio: str = "16:9"
    veo_poll_interval: float = 10.0
    veo_max_wait: float = 600.0
    veo_max_concurrent_requests: int = 2
    veo_submit_cooldown: float = 0.0
    veo_use_vertex: bool = True
    veo_project: Optional[str] = None
    veo_location: str = "us-central1"
    veo_credentials_path: Optional[Path] = Path("google-api-key.json")

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
    media_client: SoraClient | VeoClient
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

        provider = config.media_provider.lower()
        if provider == "sora":
            media_client: SoraClient | VeoClient = SoraClient(
                asset_dir=data_root / "media/sora_clips",
                api_key=os.getenv(config.sora_api_key_env),
                model=config.sora_model,
                size=config.sora_size,
                poll_interval=config.sora_poll_interval,
                request_timeout=config.sora_request_timeout,
                max_wait=config.sora_max_wait,
            )
        elif provider == "veo":
            media_client = VeoClient(
                asset_dir=data_root / "media/veo_clips",
                api_key=os.getenv(config.veo_api_key_env),
                model=config.veo_model,
                aspect_ratio=config.veo_aspect_ratio,
                poll_interval=config.veo_poll_interval,
                max_wait=config.veo_max_wait,
                max_concurrent_requests=config.veo_max_concurrent_requests,
                submit_cooldown=config.veo_submit_cooldown,
                use_vertex=config.veo_use_vertex,
                project=config.veo_project,
                location=config.veo_location,
                credentials_path=config.veo_credentials_path,
            )
        else:
            raise ValueError(f"Unsupported media_provider '{config.media_provider}'")

        return cls(
            config=config,
            article_ingestor=ArticleIngestor(),
            script_engine=ScriptEngine(llm=config.build_llm()),
            chunk_planner=ChunkPlanner(),
            prompt_builder=PromptBuilder(default_voice=config.voice_id, negative_prompt=config.negative_prompt),
            media_client=media_client,
            voice_manager=VoiceSessionManager(base_dir=data_root / "media/voice"),
            stitcher=Stitcher(export_dir=data_root / "exports"),
        )

    def run(
        self,
        article_url: str,
        output_dir: Path,
        dry_run: bool = True,
        prompts_only: bool = False,
    ) -> PipelineBundle:
        logger.info("Ingesting article: %s", article_url)
        article = self.article_ingestor.ingest(article_url)

        logger.info("Generating suspenseful script")
        script = self.script_engine.generate_script(article)

        logger.info("Planning Sora-sized chunks")
        chunks = self.chunk_planner.plan(script)

        logger.info("Building structured prompts")
        prompts = self.prompt_builder.build(article, script, chunks)

        base_bundle = PipelineBundle(
            article=article,
            script=script,
            chunks=chunks,
            prompts=prompts,
            sora_assets=[],
            voice_transcript=None,
            final_video=None,
        )
        return self.execute_prompts(
            bundle=base_bundle,
            output_dir=output_dir,
            dry_run=dry_run,
            prompts_only=prompts_only,
        )

    def execute_prompts(
        self,
        bundle: PipelineBundle,
        output_dir: Path,
        dry_run: bool = True,
        prompts_only: bool = False,
    ) -> PipelineBundle:
        prompts = bundle.prompts
        chunks = bundle.chunks

        voice_asset = bundle.voice_transcript
        if prompts_only:
            logger.info("Prompts-only mode: skipping voice preparation")
        else:
            logger.info("Ensuring voice consistency")
            if prompts.sora_prompts:
                directive = prompts.sora_prompts[0].cameo_voice
                if directive:
                    voice_text = "\n\n".join(chunk.transcript for chunk in chunks.chunks)
                    voice_asset = self.voice_manager.prepare_voice(directive, voice_text, dry_run=dry_run)
                else:
                    logger.warning("No voice directive supplied; skipping voice preparation")

        provider = self.config.media_provider.lower()
        if prompts_only:
            logger.info("Prompts-only mode: skipping media submission")
            media_assets: list[Path] = []
        else:
            if provider == "sora":
                real_sora = self.config.use_real_sora and not dry_run
                if real_sora and not getattr(self.media_client, "api_key", None):
                    raise RuntimeError(
                        f"Missing Sora API key. Set {self.config.sora_api_key_env} in your environment."
                    )
                submit_dry_run = not real_sora
                logger.info("Submitting prompts to Sora (dry_run=%s)", submit_dry_run)
                media_assets = self.media_client.submit_prompts(prompts.sora_prompts, dry_run=submit_dry_run)
            elif provider == "veo":
                if dry_run:
                    logger.info("Dry run: skipping Veo submission")
                    media_assets = self.media_client.submit_prompts(prompts.sora_prompts, dry_run=True)
                else:
                    if not getattr(self.media_client, "api_key", None):
                        raise RuntimeError(
                            f"Missing Veo API key. Set {self.config.veo_api_key_env} in your environment."
                        )
                    logger.info("Submitting prompts to Veo model %s", self.config.veo_model)
                    media_assets = self.media_client.submit_prompts(prompts.sora_prompts, dry_run=False)
            else:
                raise ValueError(f"Unsupported media_provider '{self.config.media_provider}'")

        final_video = bundle.final_video
        should_stitch = (
            not prompts_only
            and not dry_run
            and (
                (provider == "sora" and self.config.use_real_sora)
                or (provider == "veo")
            )
            and media_assets
        )
        if should_stitch:
            final_video = self.stitcher.stitch(media_assets, voice_asset)
        else:
            reason = "prompts-only mode" if prompts_only else "dry run or no assets"
            logger.info("Skipping stitching (%s)", reason)

        output_dir.mkdir(parents=True, exist_ok=True)
        return bundle.model_copy(
            update={
                "sora_assets": media_assets,
                "voice_transcript": voice_asset,
                "final_video": final_video,
            }
        )
