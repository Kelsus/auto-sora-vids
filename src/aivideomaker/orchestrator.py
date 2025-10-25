from __future__ import annotations

import json
import logging
import os
import shutil
import sys
import textwrap
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any, Optional

from dotenv import find_dotenv, load_dotenv
from pydantic import BaseModel, Field

from aivideomaker.article_ingest.model import ArticleBundle, slugify
from aivideomaker.article_ingest.service import ArticleIngestor
from aivideomaker.chunker.model import ChunkPlan
from aivideomaker.chunker.planner import ChunkPlanner
from aivideomaker.prompt_builder.builder import MediaPromptBuilder
from aivideomaker.prompt_builder.model import MediaPrompt, MediaPromptBundle
from aivideomaker.script_engine.engine import ScriptEngine
from aivideomaker.script_engine.llm import ClaudeLLM, EchoLLM, LLMClient
from aivideomaker.script_engine.model import Beat, BeatQCRules, BeatVisualSpec, ScriptPlan, SocialCaption
from aivideomaker.script_engine.utils import load_json_with_repair
from aivideomaker.script_engine.reviewer import ScriptReviewDecision, ScriptReviewer
from aivideomaker.media_pipeline.chart_renderer import ChartRenderer
from aivideomaker.media_pipeline.elevenlabs_client import ElevenLabsClient
from aivideomaker.media_pipeline.sora_client import SoraClient
from aivideomaker.media_pipeline.still_image_client import StillImageClient
from aivideomaker.media_pipeline.veo_client import VeoClient
from aivideomaker.media_pipeline.voice import VoiceSessionManager, NarrationAsset
from aivideomaker.media_pipeline.elevenlabs_music_client import ElevenLabsMusicClient
from aivideomaker.orchestrator_chart_models import ChartSpec
from aivideomaker.stitcher.assembler import Stitcher, CaptionSegment
from aivideomaker.captions.ass_builder import write_karaoke_ass
from moviepy.editor import ImageClip


_dotenv_path = find_dotenv(usecwd=True)
if _dotenv_path:
    load_dotenv(_dotenv_path, override=False)

logger = logging.getLogger(__name__)


class ScriptRejectedError(RuntimeError):
    """Raised when a script is rejected and the pipeline should halt gracefully."""


class VisualStyle(BaseModel):
    palette: str
    grain: bool = False
    lens: Optional[str] = None
    motion: list[str] = Field(default_factory=list)
    bans: list[str] = Field(default_factory=list)


class AudioStyle(BaseModel):
    vo_lufs: float
    true_peak_db: float
    music_sidechain_db: float
    sfx_usage_max: int


class CaptionStyleSpec(BaseModel):
    outline: bool = True
    shadow: bool = True
    max_lines: int = 2
    max_chars_per_line: int = 34
    position: Optional[str] = None
    weight: Optional[str] = None


class CaptionStyles(BaseModel):
    default: CaptionStyleSpec
    data: Optional[CaptionStyleSpec] = None


class StyleBible(BaseModel):
    visual: VisualStyle
    audio: AudioStyle
    captions: CaptionStyles


class BeatsMetaDefaults(BaseModel):
    min_duration_sec: float
    max_cuts_in_row_lt_1p7s: int
    caption_region: str


class ChartSpec(BaseModel):
    library: str
    width: int
    height: int
    data: dict[str, Any]
    mark: str
    encoding: dict[str, Any]
    style: Optional[str] = None


class QualityControlRuleSet(BaseModel):
    min_shot_sec: float
    prefer_shot_sec: list[float] = Field(default_factory=list)
    flag_if_text_when_forbidden: bool = True
    flag_split_screen: bool = True
    flag_flicker_threshold: float = 0.12
    style_refset_id: Optional[str] = None
    reject_if_clip_similarity_below: Optional[float] = None


class BeatOverride(BaseModel):
    intent: Optional[str] = None
    visual: Optional[BeatVisualSpec] = None
    qc: Optional[BeatQCRules] = None
    caption_region: Optional[str] = None
    min_duration_sec: Optional[float] = None


class StyleTemplate(BaseModel):
    style_bible: StyleBible
    beats_meta_defaults: Optional[BeatsMetaDefaults] = None
    chart_specs: dict[str, ChartSpec] = Field(default_factory=dict)
    qc_ruleset: Optional[QualityControlRuleSet] = None
    beat_overrides: dict[str, BeatOverride] = Field(default_factory=dict)


class PipelineConfig(BaseModel):
    data_root: Path = Path("data/runs")
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
    enable_script_review: bool = True
    require_human_approval: bool = True
    # Sora configuration
    sora_model: str = "sora-2"
    sora_size: str = "720x1280"
    sora_api_key_env: str = "OPENAI_API_KEY"
    sora_poll_interval: float = 10.0
    sora_request_timeout: float = 60.0
    sora_max_wait: float = 2400.0
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
    veo_credentials_path: Optional[Path] = None
    veo_credentials_parameter: Optional[str] = None
    style_template_path: Optional[Path] = None

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
        logger.warning("⚠️  Unknown llm_provider '%s'; falling back to EchoLLM", provider)
        return EchoLLM()


class PipelineBundle(BaseModel):
    article: ArticleBundle
    script: ScriptPlan
    script_review: ScriptReviewDecision | None = None
    chunks: ChunkPlan
    prompts: MediaPromptBundle
    sora_assets: list[Path]
    voice_transcript: Optional[Path]
    narration_audio: Optional[Path] = None
    narration_alignment: Optional[Path] = None
    narration_alignment_payload: Optional[dict] = None
    music_track: Optional[Path] = None
    social_caption_path: Optional[Path] = None
    final_video: Optional[Path]
    script_greenlit: bool = True
    human_approval: Optional[bool] = None
    style_bible: Optional[StyleBible] = None
    beats_meta_defaults: Optional[BeatsMetaDefaults] = None
    chart_specs: dict[str, ChartSpec] = Field(default_factory=dict)
    qc_ruleset: Optional[QualityControlRuleSet] = None


class PromptGenerationResult(BaseModel):
    bundle: PipelineBundle
    clip_ids: list[str]


class ClipRenderResult(BaseModel):
    bundle: PipelineBundle
    clip_id: str
    clip_asset: Path


@dataclass
class PipelineOrchestrator:
    config: PipelineConfig
    article_ingestor: ArticleIngestor
    script_engine: ScriptEngine
    script_reviewer: ScriptReviewer
    chunk_planner: ChunkPlanner
    prompt_builder: MediaPromptBuilder
    media_client: SoraClient | VeoClient
    voice_manager: VoiceSessionManager
    music_client: ElevenLabsMusicClient | None
    stitcher: Stitcher
    style_template: Optional[StyleTemplate] = None
    still_image_client: Optional[StillImageClient] = None
    chart_renderer: Optional[ChartRenderer] = None

    @classmethod
    def from_file(cls, path: Path) -> "PipelineOrchestrator":
        config = PipelineConfig.from_file(path)
        return cls.default(config)

    @classmethod
    def default(cls, config: PipelineConfig | None = None) -> "PipelineOrchestrator":
        config = config or PipelineConfig()
        data_root = config.data_root
        placeholder_root = data_root / ".placeholder"

        provider = config.media_provider.lower()
        if provider == "sora":
            media_client: SoraClient | VeoClient = SoraClient(
                asset_dir=None,
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
                asset_dir=None,
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
                credentials_parameter=config.veo_credentials_parameter,
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
                    logger.error("💥  Failed to initialize ElevenLabs client: %s", exc)
            else:
                logger.warning(
                    "⚠️  Narration voice configured but no ElevenLabs API key found in %s",
                    config.elevenlabs_api_key_env,
                )

        music_client: ElevenLabsMusicClient | None = None
        if config.use_music:
            music_key = os.getenv(config.music_api_key_env) or os.getenv("ELEVEN_LABS_API_KEY")
            if music_key:
                try:
                    music_client = ElevenLabsMusicClient(
                        api_key=music_key,
                        output_dir=placeholder_root / "music",
                        model_id=config.music_model_id,
                        force_instrumental=config.music_force_instrumental,
                        output_format=config.music_output_format,
                        request_timeout=config.music_request_timeout,
                    )
                except ValueError as exc:
                    logger.error("💥  Failed to initialize ElevenLabs music client: %s", exc)
            else:
                logger.warning(
                    "⚠️  Music generation enabled but no ElevenLabs API key found in %s",
                    config.music_api_key_env,
                )

        llm_client = config.build_llm()
        style_template = cls._load_style_template(config)
        visual_style_dict = (
            style_template.style_bible.visual.model_dump()
            if style_template and style_template.style_bible
            else None
        )
        still_client = StillImageClient(
            asset_dir=placeholder_root / "stills",
            api_key=os.getenv(config.veo_api_key_env),
            use_vertex=config.veo_use_vertex,
            project=config.veo_project,
            location=config.veo_location,
        )
        chart_renderer = ChartRenderer(placeholder_root / "charts")

        return cls(
            config=config,
            article_ingestor=ArticleIngestor(),
            script_engine=ScriptEngine(llm=llm_client),
            script_reviewer=ScriptReviewer(llm=llm_client),
            chunk_planner=ChunkPlanner(),
            prompt_builder=MediaPromptBuilder(
                default_voice=config.voice_id,
                negative_prompt=config.negative_prompt,
                visual_style=visual_style_dict,
            ),
            media_client=media_client,
            voice_manager=VoiceSessionManager(
                base_dir=placeholder_root / "voice",
                eleven_client=elevenlabs_client,
                default_voice_id=narration_voice_id,
            ),
            music_client=music_client,
            stitcher=Stitcher(export_dir=placeholder_root / "exports"),
            style_template=style_template,
            still_image_client=still_client,
            chart_renderer=chart_renderer,
        )

    @staticmethod
    def _load_style_template(config: PipelineConfig) -> Optional[StyleTemplate]:
        candidate_paths: list[Path] = []
        if config.style_template_path:
            path = config.style_template_path
            if not path.is_absolute():
                path = Path.cwd() / path
            candidate_paths.append(path)

        for candidate in candidate_paths:
            if candidate.exists():
                try:
                    payload = json.loads(candidate.read_text(encoding="utf-8"))
                    return StyleTemplate.model_validate(payload)
                except Exception as exc:  # pragma: no cover - configuration error
                    logger.error("💥  Failed to load style template from %s: %s", candidate, exc)

        try:
            template_path = resources.files("aivideomaker.assets").joinpath("style_bible_template.json")
        except (AttributeError, ModuleNotFoundError):  # pragma: no cover - python <3.9 or packaging issue
            logger.warning("⚠️  Style template resources unavailable; continuing without style metadata.")
            return None

        if not template_path.is_file():
            logger.info("ℹ️  No bundled style template found; continuing without style metadata.")
            return None

        try:
            with template_path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
            return StyleTemplate.model_validate(payload)
        except Exception as exc:  # pragma: no cover - should not happen
            logger.error("💥  Failed to parse packaged style template: %s", exc)
            return None

    def _apply_style_template(self, script: ScriptPlan) -> ScriptPlan:
        template = self.style_template
        if not template:
            return script

        overrides = template.beat_overrides
        defaults = template.beats_meta_defaults
        updated_beats = []

        for beat in script.beats:
            update: dict[str, Any] = {}
            override = overrides.get(beat.id) if overrides else None

            if override:
                if override.intent is not None:
                    update["intent"] = override.intent
                if override.visual is not None:
                    update["visual"] = override.visual
                if override.qc is not None:
                    update["qc"] = override.qc
                if override.caption_region is not None:
                    update["caption_region"] = override.caption_region
                if override.min_duration_sec is not None:
                    update["min_duration_sec"] = override.min_duration_sec

            if defaults:
                if (
                    defaults.caption_region
                    and "caption_region" not in update
                    and beat.caption_region is None
                ):
                    update["caption_region"] = defaults.caption_region
                if (
                    defaults.min_duration_sec is not None
                    and "min_duration_sec" not in update
                    and beat.min_duration_sec is None
                ):
                    update["min_duration_sec"] = defaults.min_duration_sec

            if beat.visual is None and update.get("visual") is None:
                update["visual"] = BeatVisualSpec(type="cinematic_broll")

            visual_type = None
            current_visual = update.get("visual") or beat.visual
            if current_visual:
                visual_type = current_visual.type

            if visual_type in (None, "", "cinematic_broll"):
                inferred_type = self._classify_visual_mode(beat)
                if inferred_type and inferred_type != "cinematic_broll":
                    if current_visual:
                        update["visual"] = current_visual.model_copy(update={"type": inferred_type})
                    else:
                        update["visual"] = BeatVisualSpec(type=inferred_type)

            if update:
                beat = beat.model_copy(update=update)
            updated_beats.append(beat)

        return script.model_copy(update={"beats": updated_beats})

    def _classify_visual_mode(self, beat: Beat) -> str:
        llm = getattr(self.script_engine, "llm", None)
        if llm is None:
            return "cinematic_broll"

        instructions = textwrap.dedent(
            """
            You are selecting a visual production approach for a short-form documentary beat.
            Choose one of the following categories:
              - "chart" when the narration focuses on numeric data, stats, or would benefit from a data visualization or on-screen text.
              - "still_motion" when the narration references text/quotes, detailed wording, or visuals better served by a still image with light motion.
              - "cinematic_broll" when the narration describes environments, people, or actions that should be depicted with motion footage.

            Respond with a JSON object like {"visual_type": "chart"}.
            """
        ).strip()
        payload = {
            "transcript": beat.transcript,
            "visual_seed": beat.visual_seed or "",
            "purpose": beat.purpose,
        }
        prompt = f"{instructions}\nInput:{json.dumps(payload, ensure_ascii=False)}"
        try:
            raw = llm.complete(prompt)
            data = load_json_with_repair(raw, logger=logger)
            mode = str(data.get("visual_type", "cinematic_broll")).lower()
            if mode in {"chart", "still_motion", "cinematic_broll"}:
                return mode
        except Exception as exc:  # pragma: no cover - LLM fallback
            logger.debug("Visual classification fallback due to error: %s", exc)
        return "cinematic_broll"

    def _build_initial_bundle(
        self,
        article_url: str,
        output_dir: Path,
        *,
        dry_run: bool,
        prompts_only: bool,
        cleanup: bool,
    ) -> PipelineBundle:
        logger.info("📰  Ingesting article: %s", article_url)
        article = self.article_ingestor.ingest(article_url)
        run_dirs = self._prepare_run_environment(article.slug, output_dir, cleanup)
        filename_base = article.slug
        article_title = article.article.metadata.title

        review_decision: ScriptReviewDecision | None = None
        pending_review_feedback: ScriptReviewDecision | None = None
        previous_script_attempt: ScriptPlan | None = None
        script_greenlit = not self.config.enable_script_review
        human_approval: bool | None = None
        while True:
            logger.info("✍️  Generating suspenseful script")
            script = self.script_engine.generate_script(
                article,
                review=pending_review_feedback,
                previous_script=previous_script_attempt if pending_review_feedback else None,
            )

            review_decision = None
            if self.config.enable_script_review:
                review_decision = self.script_reviewer.review(article, script)
                script_greenlit = not review_decision.requires_revision
                if review_decision.requires_revision:
                    message = self._format_review_failure(review_decision)
                    print(f"\n{message}\n")
                    if self._should_regenerate_script(
                        "Automated reviewer did not approve the script. Generate again? (y/n): "
                    ):
                        previous_script_attempt = script
                        pending_review_feedback = review_decision
                        continue
                    pending_review_feedback = None
                    previous_script_attempt = script
                    break
                pending_review_feedback = None
            else:
                script_greenlit = True
                pending_review_feedback = None

            if self.config.require_human_approval and not prompts_only:
                approved = self._require_human_approval(script, review_decision)
                if not approved:
                    if self._should_regenerate_script(
                        "Human reviewer rejected the script. Generate again? (y/n): "
                    ):
                        previous_script_attempt = script
                        continue
                    script_greenlit = False
                human_approval = approved
            elif self.config.require_human_approval:
                logger.info("🚦  Prompts-only mode active; skipping human approval gate.")
                human_approval = None
            previous_script_attempt = script

            if self.style_template:
                script = self._apply_style_template(script)
            break

        narration_asset: NarrationAsset | None = None
        alignment_payload: dict | None = None
        allow_media_generation = script_greenlit and not prompts_only
        if allow_media_generation and self.voice_manager.eleven_client:
            voice_id = self.config.narration_voice_id or self.config.voice_id
            narration_asset = self.voice_manager.prepare_voice(
                script_text=script.full_transcript,
                voice_id=voice_id,
                dry_run=dry_run,
            )
            alignment_payload = narration_asset.alignment_payload

        caption_path: Path | None = None
        if script.social_caption:
            caption_path = self._write_social_caption(
                script.social_caption,
                run_dirs["export_dir"],
                article_title=article_title,
                base_name=filename_base,
            )

        music_path: Path | None = None
        if (
            allow_media_generation
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
                logger.error("💥  Failed to generate ElevenLabs music track: %s", exc)
                music_path = None

        logger.info("🧩  Planning Veo-sized segments")
        chunks = self.chunk_planner.plan(script, alignment=alignment_payload)

        logger.info("🛠️  Building structured prompts")
        prompts = self.prompt_builder.build(article, script, chunks)

        style_bible = (
            self.style_template.style_bible.model_copy()
            if self.style_template and self.style_template.style_bible
            else None
        )
        beats_meta_defaults = (
            self.style_template.beats_meta_defaults.model_copy()
            if self.style_template and self.style_template.beats_meta_defaults
            else None
        )
        chart_specs = (
            {key: spec.model_copy() for key, spec in self.style_template.chart_specs.items()}
            if self.style_template and self.style_template.chart_specs
            else {}
        )
        qc_ruleset = (
            self.style_template.qc_ruleset.model_copy()
            if self.style_template and self.style_template.qc_ruleset
            else None
        )

        return PipelineBundle(
            article=article,
            script=script,
            script_review=review_decision,
            chunks=chunks,
            prompts=prompts,
            sora_assets=[],
            voice_transcript=(narration_asset.transcript_path if narration_asset else None),
            narration_audio=(narration_asset.audio_path if narration_asset else None),
            narration_alignment=(narration_asset.alignment_path if narration_asset else None),
            narration_alignment_payload=alignment_payload,
            music_track=music_path,
            social_caption_path=caption_path,
            final_video=None,
            script_greenlit=script_greenlit,
            human_approval=human_approval,
            style_bible=style_bible,
            beats_meta_defaults=beats_meta_defaults,
            chart_specs=chart_specs,
            qc_ruleset=qc_ruleset,
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

        base_bundle = self._build_initial_bundle(
            article_url=article_url,
            output_dir=output_dir,
            dry_run=dry_run,
            prompts_only=prompts_only,
            cleanup=cleanup,
        )
        effective_prompts_only = prompts_only or not base_bundle.script_greenlit
        return self.execute_prompts(
            bundle=base_bundle,
            output_dir=output_dir,
            dry_run=dry_run,
            prompts_only=effective_prompts_only,
            cleanup=False,
        )

    def generate_prompt_bundle(
        self,
        article_url: str,
        output_dir: Path,
        *,
        dry_run: bool = True,
        cleanup: bool = False,
    ) -> PromptGenerationResult:
        base_bundle = self._build_initial_bundle(
            article_url=article_url,
            output_dir=output_dir,
            dry_run=dry_run,
            prompts_only=True,
            cleanup=cleanup,
        )
        prompt_bundle = self.execute_prompts(
            bundle=base_bundle,
            output_dir=output_dir,
            dry_run=dry_run,
            prompts_only=True,
            cleanup=False,
        )
        clip_ids = [prompt.chunk_id for prompt in prompt_bundle.prompts.media_prompts]
        return PromptGenerationResult(bundle=prompt_bundle, clip_ids=clip_ids)

    def render_clip(
        self,
        bundle: PipelineBundle,
        clip_id: str,
        output_dir: Path,
        *,
        dry_run: bool = True,
    ) -> ClipRenderResult:
        run_dirs = self._prepare_run_environment(bundle.article.slug, output_dir, cleanup=False)
        prompt = next((p for p in bundle.prompts.media_prompts if p.chunk_id == clip_id), None)
        if not prompt:
            logger.warning("Prompt missing for clip %s; constructing fallback prompt.", clip_id)
            prompt = self._fallback_prompt(bundle, clip_id)

        chunk_ref = next(
            (c for c in bundle.chunks.chunks if getattr(c, "id", c.beat_id) == clip_id),
            None,
        )
        beat_id = chunk_ref.beat_id if chunk_ref else clip_id
        beat = next((b for b in bundle.script.beats if b.id == beat_id), None)
        visual_mode = self._resolve_visual_mode(beat)

        existing_clip = self._existing_clip_path(run_dirs, clip_id)
        if existing_clip:
            logger.info("♻️  Reusing existing clip for %s at %s", clip_id, existing_clip)
            return ClipRenderResult(bundle=bundle, clip_id=clip_id, clip_asset=existing_clip)

        clip_path: Optional[Path] = None

        if visual_mode == "chart":
            clip_path = self._render_chart_clip(bundle, beat, run_dirs, clip_id, prompt)
        if clip_path is None and visual_mode in {"still_motion", "still"}:
            clip_path = self._render_still_clip(prompt, run_dirs, clip_id)

        provider = self.config.media_provider.lower()
        if clip_path is None:
            if provider == "sora":
                real_sora = not dry_run
                if real_sora and not getattr(self.media_client, "api_key", None):
                    raise RuntimeError(
                        f"Missing Sora API key. Set {self.config.sora_api_key_env} in your environment."
                    )
                submit_dry_run = not real_sora
                logger.info("🎬  Rendering clip %s via Sora (dry_run=%s)", clip_id, submit_dry_run)
                media_assets = self.media_client.submit_prompts([prompt], dry_run=submit_dry_run)
            elif provider == "veo":
                if dry_run:
                    logger.info("🧪  Dry run: skipping Veo submission for %s", clip_id)
                    media_assets = self.media_client.submit_prompts([prompt], dry_run=True)
                else:
                    has_key = bool(getattr(self.media_client, "api_key", None))
                    uses_vertex = bool(getattr(self.media_client, "use_vertex", False))
                    if not has_key and not uses_vertex:
                        raise RuntimeError(
                            f"Missing Veo API key. Set {self.config.veo_api_key_env} in your environment."
                        )
                    logger.info("🎬  Rendering clip %s via Veo model %s", clip_id, self.config.veo_model)
                    media_assets = self.media_client.submit_prompts([prompt], dry_run=False)
            else:
                raise ValueError(f"Unsupported media_provider '{self.config.media_provider}'")

            if not media_assets:
                raise RuntimeError(f"Media client returned no assets for {clip_id}")
            clip_path = Path(media_assets[0])

        try:
            stored_asset = clip_path.relative_to(run_dirs["run_dir"])
        except ValueError:
            stored_asset = clip_path

        existing_assets = [Path(asset) for asset in bundle.sora_assets]
        filtered_assets = [asset for asset in existing_assets if asset.stem != clip_id]
        updated_assets = filtered_assets + [stored_asset]
        updated_bundle = bundle.model_copy(update={"sora_assets": updated_assets})
        return ClipRenderResult(bundle=updated_bundle, clip_id=clip_id, clip_asset=clip_path)

    def stitch_bundle(
        self,
        bundle: PipelineBundle,
        output_dir: Path,
        *,
        dry_run: bool = True,
    ) -> PipelineBundle:
        return self.execute_prompts(
            bundle=bundle,
            output_dir=output_dir,
            dry_run=dry_run,
            prompts_only=False,
            cleanup=False,
            stitch_only=True,
        )

    def _render_music_prompt(self, article: ArticleBundle, script: ScriptPlan) -> str:
        if self.config.music_prompt:
            return self.config.music_prompt
        mood = ", ".join({beat.audio_mood for beat in script.beats if beat.audio_mood}) or "suspenseful investigative tone"
        return (
            f"Suspenseful investigative score with gradual build, supporting a story about {article.article.metadata.title}. "
            f"Mood cues: {mood}."
        )

    def _resolve_visual_mode(self, beat: Beat | None) -> str:
        if not beat or not beat.visual or not beat.visual.type:
            return "cinematic_broll"
        return beat.visual.type.lower()

    def _render_still_clip(
        self,
        prompt: MediaPrompt,
        run_dirs: dict[str, Path],
        clip_id: str,
    ) -> Path:
        if not self.still_image_client:
            raise RuntimeError("Still image client is not configured")
        image_path = self.still_image_client.generate(prompt.visual_prompt, prompt.negative_prompt, clip_id)
        if not Path(image_path).is_absolute():
            image_path = run_dirs["stills_dir"] / Path(image_path)
        duration = max(float(prompt.duration_sec or 3.0), 1.5)
        return self._image_to_video(Path(image_path), run_dirs["sora_dir"], clip_id, duration)

    def _render_chart_clip(
        self,
        bundle: PipelineBundle,
        beat: Beat | None,
        run_dirs: dict[str, Path],
        clip_id: str,
        prompt: MediaPrompt,
    ) -> Optional[Path]:
        if not self.chart_renderer or not beat or not beat.visual or not beat.visual.spec_id:
            return None
        spec = bundle.chart_specs.get(beat.visual.spec_id)
        if not spec:
            logger.warning("No chart spec '%s' found; falling back to still generation", beat.visual.spec_id)
            return None
        image_path = self.chart_renderer.render(spec, clip_id)
        duration = max(float(prompt.duration_sec or 3.0), 2.0)
        return self._image_to_video(Path(image_path), run_dirs["sora_dir"], clip_id, duration)

    def _image_to_video(
        self,
        image_path: Path,
        target_dir: Path,
        clip_id: str,
        duration: float,
    ) -> Path:
        target_dir.mkdir(parents=True, exist_ok=True)
        output = target_dir / f"{clip_id}.mp4"
        clip = ImageClip(str(image_path))
        clip = clip.set_duration(duration).resize(height=1920)
        if clip.w > 1080:
            clip = clip.crop(x_center=clip.w / 2, width=1080)
        elif clip.w < 1080:
            clip = clip.resize(width=1080)
        zoom_factor = 0.05
        clip = clip.resize(lambda t: 1 + zoom_factor * (t / max(duration, 1.0)))
        clip = clip.set_fps(30)
        clip.write_videofile(str(output), codec="libx264", audio=False, bitrate="4000k", logger=None)
        clip.close()
        return output

    def _fallback_prompt(self, bundle: PipelineBundle, clip_id: str) -> MediaPrompt:
        chunk = next((c for c in bundle.chunks.chunks if getattr(c, "id", c.beat_id) == clip_id), None)
        beat = next((b for b in bundle.script.beats if b.id == (chunk.beat_id if chunk else clip_id)), None)
        visual_prompt = "Documentary still shot." if not beat else beat.transcript
        return MediaPrompt(
            chunk_id=clip_id,
            transcript=chunk.transcript if chunk else (beat.transcript if beat else ""),
            visual_prompt=visual_prompt,
            audio_prompt="Ambient bed, allow narration space",
            duration_sec=chunk.estimated_duration_sec if chunk else 3.0,
        )

    def _existing_clip_path(self, run_dirs: dict[str, Path], clip_id: str) -> Path | None:
        candidates = [
            run_dirs["sora_dir"] / f"{clip_id}.mp4",
            run_dirs["veo_dir"] / f"{clip_id}.mp4",
        ]
        for path in candidates:
            if path.exists():
                return path
        return None

    def _format_review_failure(self, decision: ScriptReviewDecision) -> str:
        lines = [
            "Script failed automated review and cannot proceed to media generation.",
            f"Verdict: {decision.verdict}",
        ]
        if decision.summary:
            lines.append(f"Summary: {decision.summary}")
        if decision.concerns:
            lines.append("Concerns:")
            lines.extend(f"  - {item}" for item in decision.concerns)
        if decision.action_items:
            lines.append("Action items:")
            lines.extend(f"  - {item}" for item in decision.action_items)
        return "\n".join(lines)

    def _require_human_approval(
        self,
        script: ScriptPlan,
        decision: ScriptReviewDecision | None,
    ) -> bool:
        if not sys.stdin.isatty():
            raise RuntimeError(
                "Human approval is required but no interactive terminal was detected. "
                "Disable `require_human_approval` in the pipeline config or run from an interactive shell."
            )

        logger.info("🧑‍⚖️  Awaiting human approval for the generated script.")

        print("\n=== Automated Review Summary ===")
        if decision:
            print(f"Verdict: {decision.verdict}")
            if decision.summary:
                print(f"Summary: {decision.summary}")
            if decision.strengths:
                print("\nStrengths:")
                for item in decision.strengths:
                    print(f"- {item}")
            if decision.concerns:
                print("\nConcerns:")
                for item in decision.concerns:
                    print(f"- {item}")
            if decision.action_items:
                print("\nAction Items:")
                for item in decision.action_items:
                    print(f"- {item}")
        else:
            print("Automated review disabled; proceeding directly to human approval.")

        print("\n=== Script Premise ===")
        print(script.premise.strip())

        print("\n=== Full Script ===")
        for idx, beat in enumerate(script.beats, start=1):
            header = f"[Beat {idx}] {beat.id} | {beat.purpose} | ~{beat.estimated_duration_sec:.1f}s | Suspense {beat.suspense_level}"
            print(header)
            print(textwrap.fill(beat.transcript.strip(), width=100))
            if beat.audio_mood or beat.visual_seed:
                cues: list[str] = []
                if beat.visual_seed:
                    cues.append(f"Visual: {beat.visual_seed}")
                if beat.audio_mood:
                    cues.append(f"Audio: {beat.audio_mood}")
                print("  " + " | ".join(cues))
            print("")

        print("=== End of Script ===\n")

        while True:
            response = input("Approve the script for media generation? (y/n): ").strip().lower()
            if response in {"y", "yes"}:
                return True
            if response in {"n", "no"}:
                return False
            print("Please respond with 'y' or 'n'.")

    def _should_regenerate_script(self, prompt: str) -> bool:
        if not sys.stdin.isatty():
            logger.warning("⚠️  Cannot prompt for regeneration without an interactive terminal.")
            return False

        while True:
            response = input(prompt).strip().lower()
            if response in {"y", "yes"}:
                return True
            if response in {"n", "no"}:
                return False
            print("Please respond with 'y' or 'n'.")

    def _prepare_run_environment(self, slug: str, output_dir: Path, cleanup: bool) -> dict[str, Path]:
        run_dir = output_dir / slug
        if cleanup and run_dir.exists():
            shutil.rmtree(run_dir)

        scripts_dir = run_dir
        media_dir = run_dir / "media"
        sora_dir = media_dir / "sora_clips"
        veo_dir = media_dir / "veo_clips"
        stills_dir = media_dir / "stills"
        charts_dir = media_dir / "charts"
        voice_dir = media_dir / "voice"
        music_dir = media_dir / "music"
        export_dir = run_dir / "exports"

        for path in (sora_dir, veo_dir, stills_dir, charts_dir, voice_dir, music_dir, export_dir):
            path.mkdir(parents=True, exist_ok=True)

        # Update client destinations to new per-run directories
        if isinstance(self.media_client, SoraClient):
            self.media_client.asset_dir = sora_dir
        elif isinstance(self.media_client, VeoClient):
            self.media_client.asset_dir = veo_dir
        if self.still_image_client:
            self.still_image_client.asset_dir = stills_dir
        if self.chart_renderer:
            self.chart_renderer.output_dir = charts_dir
            self.chart_renderer.output_dir.mkdir(parents=True, exist_ok=True)
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
            "veo_dir": veo_dir,
            "stills_dir": stills_dir,
            "charts_dir": charts_dir,
            "voice_dir": voice_dir,
            "music_dir": music_dir,
            "export_dir": export_dir,
            "media_dir": media_dir,
        }

    def _write_social_caption(
        self,
        caption: SocialCaption,
        export_dir: Path,
        *,
        article_title: str,
        base_name: str,
    ) -> Path:
        export_dir.mkdir(parents=True, exist_ok=True)
        description = caption.description.strip()
        tags = []
        for tag in caption.hashtags:
            normalized = tag.strip()
            if not normalized:
                continue
            if not normalized.startswith("#"):
                normalized = f"#{normalized}"
            tags.append(normalized)
        payload = {
            "title": article_title.strip(),
            "caption": description,
            "hashtags": tags,
            "callToActionUrl": None,
            "allowLinkedInHashtags": False,
            "scheduleAt": None,
            "perChannelOverrides": {
                "linkedin": {
                    "caption": description,
                }
            },
        }
        output = export_dir / f"{base_name}.json"
        output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        return output

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
        for prompt in bundle.prompts.media_prompts:
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

        caption_path: Path | None = None
        if bundle.script.social_caption:
            existing = bundle.social_caption_path
            if existing:
                candidate = Path(existing)
            else:
                candidate = run_dirs["export_dir"] / f"{bundle.article.slug}.json"
            if not candidate.exists():
                caption_path = self._write_social_caption(
                    bundle.script.social_caption,
                    run_dirs["export_dir"],
                    article_title=bundle.article.article.metadata.title,
                    base_name=bundle.article.slug,
                )
            else:
                caption_path = candidate

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
            logger.info("🎙️  Preparing narration audio")
            script_text = bundle.script.full_transcript
            voice_id = self.config.narration_voice_id or self.config.voice_id
            if script_text.strip() and self.voice_manager.eleven_client:
                narration_asset = self.voice_manager.prepare_voice(
                    script_text=script_text,
                    voice_id=voice_id,
                    dry_run=dry_run,
                )
            else:
                logger.warning("⚠️  Narration synthesis skipped (missing text or ElevenLabs client)")

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
                logger.error("💥  Failed to generate ElevenLabs music track during execution: %s", exc)

        provider = self.config.media_provider.lower()
        if prompts_only:
            logger.info("🚧  Prompts-only mode: skipping media submission")
            media_assets: list[Path] = []
        elif stitch_only:
            media_assets = self._collect_existing_assets(bundle, run_dirs["sora_dir"])
        else:
            if provider == "sora":
                real_sora = not dry_run
                if real_sora and not getattr(self.media_client, "api_key", None):
                    raise RuntimeError(
                        f"Missing Sora API key. Set {self.config.sora_api_key_env} in your environment."
                    )
                submit_dry_run = not real_sora
                logger.info("🎬  Submitting prompts to Sora (dry_run=%s)", submit_dry_run)
                media_assets = self.media_client.submit_prompts(prompts.media_prompts, dry_run=submit_dry_run)
            elif provider == "veo":
                if dry_run:
                    logger.info("🧪  Dry run: skipping Veo submission")
                    media_assets = self.media_client.submit_prompts(prompts.media_prompts, dry_run=True)
                else:
                    has_key = bool(getattr(self.media_client, "api_key", None))
                    uses_vertex = bool(getattr(self.media_client, "use_vertex", False))
                    if not has_key and not uses_vertex:
                        raise RuntimeError(
                            f"Missing Veo API key. Set {self.config.veo_api_key_env} in your environment."
                        )
                    logger.info("🎬  Submitting prompts to Veo model %s", self.config.veo_model)
                    media_assets = self.media_client.submit_prompts(prompts.media_prompts, dry_run=False)
            else:
                raise ValueError(f"Unsupported media_provider '{self.config.media_provider}'")

        # Build captions: prefer ASS karaoke when alignment is present
        caption_segments: list[CaptionSegment] = []
        captions_ass_path: Path | None = None
        if bundle.narration_alignment_payload:
            try:
                # Derive play resolution from Sora/Veo settings when possible
                play_res = (720, 1280)
                try:
                    if self.config.media_provider.lower() == "sora" and self.config.sora_size:
                        w, h = self.config.sora_size.lower().split("x")
                        play_res = (int(w), int(h))
                except Exception:
                    pass
                captions_ass_path = write_karaoke_ass(
                    script=bundle.script,
                    alignment=bundle.narration_alignment_payload,
                    chunks=bundle.chunks,
                    export_dir=run_dirs["export_dir"],
                    play_res=play_res,
                )
                logger.info("Generated karaoke captions at %s", captions_ass_path)
            except Exception as exc:  # pragma: no cover - defensive path
                logger.warning("Failed to generate ASS captions; falling back to disabled captions: %s", exc)
        else:
            caption_segments = self._build_captions(bundle.chunks)
            if caption_segments:
                self._write_captions_file(caption_segments, run_dirs["export_dir"] / "captions.srt")

        final_video = bundle.final_video
        should_stitch = (
            (stitch_only or (not prompts_only and not dry_run))
            and (
                (provider == "sora" and (stitch_only))
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
                captions_ass=captions_ass_path,
                output_basename=bundle.article.slug,
            )
        else:
            reason = "prompts-only mode" if prompts_only else "dry run or no assets"
            logger.info("⏭️  Skipping stitching (%s)", reason)

        output_dir.mkdir(parents=True, exist_ok=True)
        return bundle.model_copy(
            update={
                "sora_assets": media_assets,
                "voice_transcript": narration_asset.transcript_path if narration_asset else bundle.voice_transcript,
                "narration_audio": narration_asset.audio_path if narration_asset else bundle.narration_audio,
                "narration_alignment": narration_asset.alignment_path if narration_asset else bundle.narration_alignment,
                "narration_alignment_payload": narration_asset.alignment_payload if narration_asset else bundle.narration_alignment_payload,
                "music_track": music_track,
                "social_caption_path": caption_path or bundle.social_caption_path,
                "final_video": final_video,
            }
        )
