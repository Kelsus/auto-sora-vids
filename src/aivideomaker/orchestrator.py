from __future__ import annotations

import json
import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field

from aivideomaker.article_ingest.model import ArticleBundle, slugify
from aivideomaker.article_ingest.service import ArticleIngestor
from aivideomaker.chunker.model import ChunkPlan
from aivideomaker.chunker.planner import ChunkPlanner
from aivideomaker.prompt_builder.builder import PromptBuilder
from aivideomaker.prompt_builder.model import PromptBundle
from aivideomaker.script_engine.engine import ScriptEngine
from aivideomaker.script_engine.llm import ClaudeLLM, EchoLLM, LLMClient
from aivideomaker.script_engine.model import ScriptPlan, SocialCaption
from aivideomaker.media_pipeline.elevenlabs_client import ElevenLabsClient
from aivideomaker.media_pipeline.sora_client import SoraClient
from aivideomaker.media_pipeline.veo_client import VeoClient
from aivideomaker.media_pipeline.voice import VoiceSessionManager, NarrationAsset
from aivideomaker.media_pipeline.elevenlabs_music_client import ElevenLabsMusicClient
from aivideomaker.stitcher.assembler import Stitcher, CaptionSegment

logger = logging.getLogger(__name__)


class PipelineConfig(BaseModel):
    data_root: Path = Path("data")
    voice_id: Optional[str] = None
    llm_provider: str = "claude"
    llm_model: str = "claude-sonnet-4-5"
    anthropic_api_key_env: str = "ANTHROPIC_API_KEY"
    media_provider: str = "sora"
    negative_prompt: Optional[str] = "no subtitles, no captions, no on-screen text, no watermark"
    narration_voice_id: Optional[str] = "FGY2WhTYpPnrIDTdsKH5"
    elevenlabs_api_key_env: str = "ELEVEN_LABS_API_KEY"
    narration_model_id: str = "eleven_turbo_v2"
    narration_voice_settings: dict[str, float] = Field(
        default_factory=lambda: {"stability": 0.3, "similarity_boost": 0.75}
    )
    narration_enable_timestamps: bool = True
    narration_audio_format: str = "mp3"
    use_music: bool = True
    music_api_key_env: str = "ELEVEN_LABS_API_KEY"
    music_prompt: Optional[str] = None
    music_track_duration_sec: float = 90.0
    music_model_id: str = "music_v1"
    music_force_instrumental: bool = True
    music_output_format: str = "mp3_44100_128"
    music_request_timeout: float = 120.0
    # Sora configuration
    use_real_sora: bool = False
    sora_model: str = "sora-2"
    sora_size: str = "720x1280"
    sora_api_key_env: str = "OPENAI_API_KEY"
    sora_poll_interval: float = 10.0
    sora_request_timeout: float = 30.0
    sora_max_wait: float = 600.0
    sora_submit_cooldown: float = 1.0
    # Veo configuration
    veo_model: str = "veo-3.0-generate-001"
    veo_api_key_env: str = "GOOGLE_API_KEY"
    veo_aspect_ratio: str = "9:16"
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
    narration_audio: Optional[Path] = None
    narration_alignment: Optional[Path] = None
    narration_alignment_payload: Optional[dict] = None
    music_track: Optional[Path] = None
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
    music_client: ElevenLabsMusicClient | None
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
                submit_cooldown=config.sora_submit_cooldown,
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

        narration_voice_id = config.narration_voice_id or config.voice_id
        elevenlabs_client: ElevenLabsClient | None = None
        if narration_voice_id:
            api_key = os.getenv(config.elevenlabs_api_key_env) or os.getenv("ELEVENLABS_API_KEY")
            if api_key:
                try:
                    elevenlabs_client = ElevenLabsClient(
                        api_key=api_key,
                        default_voice_id=narration_voice_id,
                        model_id=config.narration_model_id,
                        voice_settings=config.narration_voice_settings,
                        enable_timestamps=config.narration_enable_timestamps,
                        audio_format=config.narration_audio_format,
                    )
                except ValueError as exc:
                    logger.error("Failed to initialize ElevenLabs client: %s", exc)
            else:
                logger.warning(
                    "Narration voice configured but no ElevenLabs API key found in %s",
                    config.elevenlabs_api_key_env,
                )

        music_client: ElevenLabsMusicClient | None = None
        if config.use_music:
            music_key = os.getenv(config.music_api_key_env) or os.getenv("ELEVEN_LABS_API_KEY")
            if music_key:
                try:
                    music_client = ElevenLabsMusicClient(
                        api_key=music_key,
                        output_dir=data_root / "media/music",
                        model_id=config.music_model_id,
                        force_instrumental=config.music_force_instrumental,
                        output_format=config.music_output_format,
                        request_timeout=config.music_request_timeout,
                    )
                except ValueError as exc:
                    logger.error("Failed to initialize ElevenLabs music client: %s", exc)
            else:
                logger.warning(
                    "Music generation enabled but no ElevenLabs API key found in %s",
                    config.music_api_key_env,
                )

        return cls(
            config=config,
            article_ingestor=ArticleIngestor(),
            script_engine=ScriptEngine(llm=config.build_llm()),
            chunk_planner=ChunkPlanner(),
            prompt_builder=PromptBuilder(
                default_voice=config.voice_id,
                negative_prompt=config.negative_prompt,
            ),
            media_client=media_client,
            voice_manager=VoiceSessionManager(
                base_dir=data_root / "media/voice",
                eleven_client=elevenlabs_client,
                default_voice_id=narration_voice_id,
            ),
            music_client=music_client,
            stitcher=Stitcher(export_dir=data_root / "exports"),
        )

    def run(
        self,
        article_url: str,
        output_dir: Path,
        dry_run: bool = True,
        prompts_only: bool = False,
        cleanup: bool = False,
        stitch_only: bool = False,
    ) -> PipelineBundle:
        if stitch_only:
            bundle = self._load_existing_bundle(article_url, output_dir)
            return self.execute_prompts(
                bundle=bundle,
                output_dir=output_dir,
                dry_run=False,
                prompts_only=False,
                cleanup=False,
                stitch_only=True,
            )

        logger.info("Ingesting article: %s", article_url)
        article = self.article_ingestor.ingest(article_url)
        run_dirs = self._prepare_run_environment(article.slug, output_dir, cleanup)

        logger.info("Generating suspenseful script")
        script = self.script_engine.generate_script(article)

        narration_asset: NarrationAsset | None = None
        alignment_payload: dict | None = None
        if not prompts_only and self.voice_manager.eleven_client:
            voice_id = self.config.narration_voice_id or self.config.voice_id
            narration_asset = self.voice_manager.prepare_voice(
                script_text=script.full_transcript,
                voice_id=voice_id,
                dry_run=dry_run,
            )
            alignment_payload = narration_asset.alignment_payload

        if script.social_caption:
            self._write_social_caption(script.social_caption, run_dirs["export_dir"])

        music_path: Path | None = None
        if (
            not prompts_only
            and self.music_client
            and self.config.use_music
            and not dry_run
        ):
            try:
                prompt = self._render_music_prompt(article, script)
                music_path = self.music_client.compose(
                    prompt=prompt,
                    duration_sec=self.config.music_track_duration_sec,
                    title=article.article.metadata.title,
                )
            except Exception as exc:  # pragma: no cover - API failure path
                logger.error("Failed to generate ElevenLabs music track: %s", exc)
                music_path = None

        logger.info("Planning Veo-sized segments")
        chunks = self.chunk_planner.plan(script, alignment=alignment_payload)

        logger.info("Building structured prompts")
        prompts = self.prompt_builder.build(article, script, chunks)

        base_bundle = PipelineBundle(
            article=article,
            script=script,
            chunks=chunks,
            prompts=prompts,
            sora_assets=[],
            voice_transcript=(narration_asset.transcript_path if narration_asset else None),
            narration_audio=(narration_asset.audio_path if narration_asset else None),
            narration_alignment=(narration_asset.alignment_path if narration_asset else None),
            narration_alignment_payload=alignment_payload,
            music_track=music_path,
            final_video=None,
        )
        return self.execute_prompts(
            bundle=base_bundle,
            output_dir=output_dir,
            dry_run=dry_run,
            prompts_only=prompts_only,
            cleanup=False,
        )

    def _render_music_prompt(self, article: ArticleBundle, script: ScriptPlan) -> str:
        if self.config.music_prompt:
            return self.config.music_prompt
        mood = ", ".join({beat.audio_mood for beat in script.beats if beat.audio_mood}) or "suspenseful investigative tone"
        return (
            f"Suspenseful investigative score with gradual build, supporting a story about {article.article.metadata.title}. "
            f"Mood cues: {mood}."
        )

    def _prepare_run_environment(self, slug: str, output_dir: Path, cleanup: bool) -> dict[str, Path]:
        run_dir = output_dir / slug
        if cleanup and run_dir.exists():
            shutil.rmtree(run_dir)

        scripts_dir = run_dir
        media_dir = run_dir / "media"
        sora_dir = media_dir / "sora_clips"
        voice_dir = media_dir / "voice"
        music_dir = media_dir / "music"
        export_dir = run_dir / "exports"

        for path in (sora_dir, voice_dir, music_dir, export_dir):
            path.mkdir(parents=True, exist_ok=True)

        # Update client destinations to new per-run directories
        if isinstance(self.media_client, SoraClient):
            self.media_client.asset_dir = sora_dir
        if self.voice_manager:
            self.voice_manager.base_dir = voice_dir
        if self.music_client:
            self.music_client.output_dir = music_dir
        if self.stitcher:
            self.stitcher.export_dir = export_dir

        return {
            "run_dir": run_dir,
            "scripts_dir": scripts_dir,
            "sora_dir": sora_dir,
            "voice_dir": voice_dir,
            "music_dir": music_dir,
            "export_dir": export_dir,
        }

    def _write_social_caption(self, caption: SocialCaption, export_dir: Path) -> None:
        export_dir.mkdir(parents=True, exist_ok=True)
        description = caption.description.strip()
        tags = [tag.lstrip('#') for tag in caption.hashtags]
        hashtags = ' '.join(f"#{tag}" for tag in tags if tag)
        output = export_dir / "social_caption.txt"
        content = description
        if hashtags:
            content = f"{description}\n\n{hashtags}"
        output.write_text(content.strip() + "\n", encoding="utf-8")

    def _build_captions(self, plan: ChunkPlan, use_alignment: bool = False) -> list[CaptionSegment]:
        # Captions currently disabled; returning empty list.
        return []

    def _write_captions_file(self, captions: list[CaptionSegment], path: Path) -> None:
        return

    @staticmethod
    def _format_timestamp(seconds: float) -> str:
        total_ms = int(round(seconds * 1000))
        ms = total_ms % 1000
        total_seconds = total_ms // 1000
        s = total_seconds % 60
        total_minutes = total_seconds // 60
        m = total_minutes % 60
        h = total_minutes // 60
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    def _collect_existing_assets(self, bundle: PipelineBundle, sora_dir: Path) -> list[Path]:
        assets: list[Path] = []
        for prompt in bundle.prompts.sora_prompts:
            clip_path = sora_dir / f"{prompt.chunk_id}.mp4"
            if not clip_path.exists():
                raise RuntimeError(f"Missing clip for stitch-only mode: {clip_path}")
            assets.append(clip_path)
        return assets

    def _load_existing_bundle(self, article_url: str, output_dir: Path) -> PipelineBundle:
        slug = slugify(article_url)
        bundle_path = output_dir / slug / "bundle.json"
        if not bundle_path.exists():
            raise RuntimeError(f"Existing bundle not found at {bundle_path}")
        data = json.loads(bundle_path.read_text(encoding="utf-8"))
        return PipelineBundle.model_validate(data)

    def execute_prompts(
        self,
        bundle: PipelineBundle,
        output_dir: Path,
        dry_run: bool = True,
        prompts_only: bool = False,
        cleanup: bool = False,
        stitch_only: bool = False,
    ) -> PipelineBundle:
        run_dirs = self._prepare_run_environment(bundle.article.slug, output_dir, cleanup)
        prompts = bundle.prompts

        narration_asset: NarrationAsset | None = None
        music_track = bundle.music_track
        if bundle.narration_audio:
            if bundle.voice_transcript:
                transcript_path = Path(bundle.voice_transcript)
            else:
                transcript_dir = self.voice_manager.base_dir / "default"
                transcript_dir.mkdir(parents=True, exist_ok=True)
                transcript_path = transcript_dir / "transcript.txt"
                transcript_path.write_text(bundle.script.full_transcript, encoding="utf-8")
            audio_path = Path(bundle.narration_audio)
            alignment_path = Path(bundle.narration_alignment) if bundle.narration_alignment else None
            narration_asset = NarrationAsset(
                transcript_path=transcript_path,
                audio_path=audio_path,
                alignment_path=alignment_path,
                alignment_payload=bundle.narration_alignment_payload,
            )
        elif not prompts_only:
            logger.info("Preparing narration audio")
            script_text = bundle.script.full_transcript
            voice_id = self.config.narration_voice_id or self.config.voice_id
            if script_text.strip() and self.voice_manager.eleven_client:
                narration_asset = self.voice_manager.prepare_voice(
                    script_text=script_text,
                    voice_id=voice_id,
                    dry_run=dry_run,
                )
            else:
                logger.warning("Narration synthesis skipped (missing text or ElevenLabs client)")

        if (
            self.music_client
            and self.config.use_music
            and not dry_run
            and not prompts_only
            and music_track is None
        ):
            try:
                prompt = self._render_music_prompt(bundle.article, bundle.script)
                music_track = self.music_client.compose(
                    prompt=prompt,
                    duration_sec=self.config.music_track_duration_sec,
                    title=bundle.article.article.metadata.title,
                )
            except Exception as exc:
                logger.error("Failed to generate ElevenLabs music track during execution: %s", exc)

        provider = self.config.media_provider.lower()
        if prompts_only:
            logger.info("Prompts-only mode: skipping media submission")
            media_assets: list[Path] = []
        elif stitch_only:
            media_assets = self._collect_existing_assets(bundle, run_dirs["sora_dir"])
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
                    has_key = bool(getattr(self.media_client, "api_key", None))
                    uses_vertex = bool(getattr(self.media_client, "use_vertex", False))
                    if not has_key and not uses_vertex:
                        raise RuntimeError(
                            f"Missing Veo API key. Set {self.config.veo_api_key_env} in your environment."
                        )
                    logger.info("Submitting prompts to Veo model %s", self.config.veo_model)
                    media_assets = self.media_client.submit_prompts(prompts.sora_prompts, dry_run=False)
            else:
                raise ValueError(f"Unsupported media_provider '{self.config.media_provider}'")

        caption_segments = []
        if bundle.narration_alignment_payload:
            caption_segments = self._build_captions(bundle.chunks, use_alignment=True)
        else:
            caption_segments = self._build_captions(bundle.chunks)
        if caption_segments:
            self._write_captions_file(caption_segments, run_dirs["export_dir"] / "captions.srt")

        final_video = bundle.final_video
        should_stitch = (
            (stitch_only or (not prompts_only and not dry_run))
            and (
                (provider == "sora" and (self.config.use_real_sora or stitch_only))
                or (provider == "veo")
            )
            and media_assets
        )
        if should_stitch:
            voice_track = narration_asset.audio_path if narration_asset else None
            final_video = self.stitcher.stitch(
                media_assets,
                voice_track,
                music_track,
                captions=caption_segments,
            )
        else:
            reason = "prompts-only mode" if prompts_only else "dry run or no assets"
            logger.info("Skipping stitching (%s)", reason)

        output_dir.mkdir(parents=True, exist_ok=True)
        return bundle.model_copy(
            update={
                "sora_assets": media_assets,
                "voice_transcript": narration_asset.transcript_path if narration_asset else bundle.voice_transcript,
                "narration_audio": narration_asset.audio_path if narration_asset else bundle.narration_audio,
                "narration_alignment": narration_asset.alignment_path if narration_asset else bundle.narration_alignment,
                "narration_alignment_payload": narration_asset.alignment_payload if narration_asset else bundle.narration_alignment_payload,
                "music_track": music_track,
                "final_video": final_video,
            }
        )
