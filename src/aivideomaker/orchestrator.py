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
from typing import Any, ClassVar, Iterable, NamedTuple, Optional

from dotenv import find_dotenv, load_dotenv
from pydantic import BaseModel, Field

from aivideomaker.article_ingest.model import ArticleBundle, slugify
from aivideomaker.chart_planner import ChartAssigner, ChartPlan, ChartPlanner, ChartIdea
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
from aivideomaker.media_pipeline.chart_ai_prompt import build_chart_codegen_spec
from aivideomaker.media_pipeline.openai_chart_client import OpenAIChartClient
from aivideomaker.media_pipeline.elevenlabs_client import ElevenLabsClient
from aivideomaker.media_pipeline.sora_client import SoraClient
from aivideomaker.media_pipeline.still_image_client import StillImageClient
from aivideomaker.media_pipeline.still_scene_prompter import ScenePrompt, StillScenePrompter
from aivideomaker.media_pipeline.veo_client import VeoClient
from aivideomaker.media_pipeline.voice import VoiceSessionManager, NarrationAsset
from aivideomaker.media_pipeline.elevenlabs_music_client import ElevenLabsMusicClient
from aivideomaker.orchestrator_chart_models import ChartSpec
from aivideomaker.stitcher.assembler import Stitcher, CaptionSegment
from aivideomaker.captions.ass_builder import write_karaoke_ass
from moviepy.editor import CompositeVideoClip, ImageClip

from PIL import Image, ImageDraw, ImageFont, ImageOps

try:  # Pillow 10 removed Image.ANTIALIAS; keep compatibility for moviepy
    from PIL import Image as _PILImage

    if not hasattr(_PILImage, "ANTIALIAS") and hasattr(_PILImage, "Resampling"):
        _PILImage.ANTIALIAS = _PILImage.Resampling.LANCZOS  # type: ignore[attr-defined]
except ImportError:  # pragma: no cover - optional dependency
    pass


_dotenv_path = find_dotenv(usecwd=True)
if _dotenv_path:
    load_dotenv(_dotenv_path, override=False)

logger = logging.getLogger(__name__)


class ScriptRejectedError(RuntimeError):
    """Raised when a script is rejected and the pipeline should halt gracefully."""


class VisualClassification(NamedTuple):
    mode: str
    data: dict[str, Any]


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
    max_automatic_charts: int = 5
    chart_analysis_excerpt_chars: int = 2600
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
    enable_openai_charts: bool = False
    openai_chart_model: str = "gpt-5"
    openai_api_key_env: str = "OPENAI_API_KEY"
    openai_assistant_id: Optional[str] = None

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
    captions_ass_path: Optional[Path] = None
    final_video: Optional[Path]
    script_greenlit: bool = True
    human_approval: Optional[bool] = None
    style_bible: Optional[StyleBible] = None
    beats_meta_defaults: Optional[BeatsMetaDefaults] = None
    chart_specs: dict[str, ChartSpec] = Field(default_factory=dict)
    qc_ruleset: Optional[QualityControlRuleSet] = None
    chart_plan: Optional[ChartPlan] = None
    chart_assignments: dict[str, str] = Field(default_factory=dict, description="Mapping of beat ids to chart ids")


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
    openai_chart_client: Optional[OpenAIChartClient] = None
    still_scene_prompter: Optional[StillScenePrompter] = None
    chart_planner: Optional[ChartPlanner] = None
    chart_assigner: Optional[ChartAssigner] = None
    _kenburns_index: int = 0

    _KENBURNS_PATTERNS: ClassVar[list[dict[str, Any]]] = [
        {
            "name": "zoom_in_center",
            "start_scale": 1.0,
            "end_scale": 1.12,
            "start_center": (0.5, 0.5),
            "end_center": (0.5, 0.5),
            "scale_strength": 1.0,
            "pan_strength": 0.0,
            "scale_ease": "ease_in_out",
            "hold": {"start": 0.12, "end": 0.12},
        },
        {
            "name": "pan_left_to_right",
            "start_scale": 1.08,
            "end_scale": 1.12,
            "start_center": (0.42, 0.5),
            "end_center": (0.6, 0.5),
            "scale_strength": 0.6,
            "pan_strength": 1.0,
            "scale_ease": "ease_in_out",
            "pan_ease": "ease_in_out",
            "hold": {"start": 0.08, "end": 0.1},
        },
        {
            "name": "zoom_out_down",
            "start_scale": 1.16,
            "end_scale": 1.0,
            "start_center": (0.5, 0.46),
            "end_center": (0.5, 0.58),
            "scale_strength": 1.0,
            "pan_strength": 0.75,
            "scale_ease": "ease_in_out",
            "pan_ease": "ease_out",
            "hold": {"start": 0.1, "end": 0.18},
        },
        {
            "name": "pan_bottom_to_top",
            "start_scale": 1.08,
            "end_scale": 1.08,
            "start_center": (0.5, 0.64),
            "end_center": (0.5, 0.36),
            "scale_strength": 0.55,
            "pan_strength": 1.0,
            "scale_ease": "ease_out",
            "pan_ease": "ease_in_out",
            "hold": {"start": 0.05, "end": 0.12},
        },
        {
            "name": "zoom_in_rule_of_thirds",
            "start_scale": 1.02,
            "end_scale": 1.16,
            "start_center": (0.38, 0.56),
            "end_center": (0.32, 0.46),
            "scale_strength": 1.0,
            "pan_strength": 0.65,
            "scale_ease": "ease_in_out",
            "pan_ease": "ease_in_out",
            "hold": {"start": 0.08, "end": 0.1},
        },
        {
            "name": "drift_diagonal",
            "start_scale": 1.1,
            "end_scale": 1.06,
            "start_center": (0.58, 0.42),
            "end_center": (0.42, 0.58),
            "scale_strength": 0.8,
            "pan_strength": 0.9,
            "scale_ease": "ease_out",
            "pan_ease": "ease_in_out",
            "hold": {"start": 0.06, "end": 0.14},
        },
    ]

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
        chart_planner = ChartPlanner(
            llm=llm_client,
            max_charts=config.max_automatic_charts,
            excerpt_chars=config.chart_analysis_excerpt_chars,
        )
        chart_assigner = ChartAssigner()
        credentials_path = config.veo_credentials_path or os.getenv("GEMINI_KEY_FILE")
        still_client = StillImageClient(
            asset_dir=placeholder_root / "stills",
            api_key=os.getenv(config.veo_api_key_env),
            use_vertex=config.veo_use_vertex,
            project=config.veo_project,
            location=config.veo_location,
            credentials_path=credentials_path,
        )
        chart_renderer = ChartRenderer(placeholder_root / "charts")
        openai_chart_client: Optional[OpenAIChartClient] = None
        if config.enable_openai_charts:
            try:
                openai_chart_client = OpenAIChartClient(
                    model=config.openai_chart_model,
                    api_key_env=config.openai_api_key_env,
                    assistant_id=config.openai_assistant_id,
                )
            except Exception as exc:
                logger.warning("OpenAI chart client disabled due to initialization error: %s", exc)
                openai_chart_client = None

        orchestrator = cls(
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
            openai_chart_client=openai_chart_client,
            still_scene_prompter=StillScenePrompter(),
            chart_planner=chart_planner,
            chart_assigner=chart_assigner,
        )
        orchestrator._kenburns_index = 0
        return orchestrator

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
        classification_history: list[dict[str, Any]] = []

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

            classification: VisualClassification | None = None
            if visual_type in (None, "", "cinematic_broll"):
                classification = self._classify_visual_mode(beat, classification_history)
                inferred_type = classification.mode
                if inferred_type and inferred_type != "cinematic_broll":
                    if current_visual:
                        new_visual = current_visual.model_copy(update={"type": inferred_type})
                    else:
                        new_visual = BeatVisualSpec(type=inferred_type)
                    new_visual = self._merge_visual_metadata(new_visual, classification.data)
                    new_visual = self._ensure_visual_defaults(new_visual, classification.data)
                    update["visual"] = new_visual

            final_visual = update.get("visual") or beat.visual

            if update:
                beat = beat.model_copy(update=update)
                final_visual = beat.visual
            updated_beats.append(beat)

            history_entry = {
                "beat_id": beat.id,
                "visual_type": (final_visual.type if final_visual else "cinematic_broll"),
                "chart_variant": getattr(final_visual, "chart_variant", None) if final_visual else None,
            }
            classification_history.append(history_entry)

        return script.model_copy(update={"beats": updated_beats})

    def _classify_visual_mode(
        self,
        beat: Beat,
        history: list[dict[str, Any]] | None = None,
    ) -> VisualClassification:
        llm = getattr(self.script_engine, "llm", None)
        if llm is None:
            return VisualClassification("cinematic_broll", {})

        history = history or []
        history_lines = []
        for item in history:
            visual_type = item.get("visual_type", "cinematic_broll")
            variant = item.get("chart_variant")
            if variant and visual_type == "chart":
                history_lines.append(f"{item['beat_id']}: chart ({variant})")
            else:
                history_lines.append(f"{item['beat_id']}: {visual_type}")
        history_text = "None yet" if not history_lines else "; ".join(history_lines)

        instructions = textwrap.dedent(
            """
            You are selecting a visual production approach for a short-form documentary beat.
            Available options for "visual_type":
              - "chart" → choose when the narration leans on data, percentages, comparisons, or is best conveyed with on-screen text/graphics.
              - "still_motion" → choose when the narration references quotes, specific wording, or any text-centric content suited to a still image plus subtle motion.
              - "cinematic_broll" → choose when the narration describes scenes, actions, places, or people that call for full-motion footage.

            Output requirements:
              - Respond with valid JSON only, without code fences or commentary.
              - Use this schema:
                {
                  "visual_type": "chart|still_motion|cinematic_broll",
                  "chart": {
                    "variant": "donut|bar|line|area|combo|other",
                    "reason": "short explanation",
                    "data_available": true|false,
                    "should_render": true|false,
                    "duplicates_previous": true|false,
                    "title": "optional headline for the chart",
                    "subtitle": "optional supporting line",
                    "x_label": "optional x-axis label",
                    "y_label": "optional y-axis label",
                    "note": "optional footnote or citation",
                    "data_points": [
                      {
                        "label": "category or timestamp",
                        "value": number,
                        "secondary_value": number | null,
                        "series": "series/group name" | null
                      }
                    ]
                  },
                  "still_motion": {
                    "focus": "primary subject for the still",
                    "reason": "short explanation"
                  }
                }
              - Only set chart.should_render to true when the beat supplies concrete, label-able data and the visualization adds something new compared to prior beats.
              - If the narration is qualitative or repeats a previous chart insight, prefer "still_motion" and explain the focus.
              - Prior visuals so far: {history_text}
            """
        ).strip()
        payload = {
            "transcript": beat.transcript,
            "visual_seed": beat.visual_seed or "",
            "purpose": beat.purpose,
            "previous_visuals": history,
        }
        prompt = f"{instructions}\nInput:{json.dumps(payload, ensure_ascii=False)}"
        try:
            raw = llm.complete(prompt)
            logger.debug("Visual classification raw response for beat %s: %s", beat.id if beat else "<unknown>", raw)
            data = load_json_with_repair(raw, logger=logger)
            metadata = {
                "chart": data.get("chart") or {},
                "still_motion": data.get("still_motion") or {},
            }
            mode = str(data.get("visual_type", "cinematic_broll")).lower()
            if mode == "chart":
                chart_info = metadata["chart"]
                data_available = bool(chart_info.get("data_available", True))
                should_render = bool(chart_info.get("should_render", True))
                duplicates_previous = bool(chart_info.get("duplicates_previous", False))
                if not data_available or not should_render:
                    fallback_mode = "still_motion" if metadata["still_motion"].get("focus") else "cinematic_broll"
                    metadata.setdefault("chart", {})["fallback"] = "insufficient_data" if not data_available else "duplicate_chart"
                    chart_info["should_render"] = should_render and data_available and not duplicates_previous
                    mode = fallback_mode
                else:
                    chart_info["should_render"] = True and not duplicates_previous
                    if duplicates_previous:
                        metadata.setdefault("chart", {})["fallback"] = "duplicate_chart"
                        fallback_mode = "still_motion" if metadata["still_motion"].get("focus") else "cinematic_broll"
                        chart_info["should_render"] = False
                        mode = fallback_mode
            if mode in {"chart", "still_motion", "cinematic_broll"}:
                return VisualClassification(mode, metadata)
        except Exception as exc:  # pragma: no cover - LLM fallback
            logger.debug("Visual classification fallback due to error: %s", exc)
        return VisualClassification("cinematic_broll", {})

    def _merge_visual_metadata(self, visual: BeatVisualSpec, metadata: dict[str, Any]) -> BeatVisualSpec:
        updates: dict[str, Any] = {}
        chart_info = metadata.get("chart") or {}
        if chart_info:
            if "variant" in chart_info:
                updates["chart_variant"] = chart_info.get("variant")
            if "reason" in chart_info:
                updates["chart_reason"] = chart_info.get("reason")
            if "data_available" in chart_info:
                updates["chart_data_available"] = chart_info.get("data_available")
            if "should_render" in chart_info:
                updates["chart_should_render"] = chart_info.get("should_render")
            if "duplicates_previous" in chart_info:
                updates["chart_duplicates_previous"] = chart_info.get("duplicates_previous")
            if "title" in chart_info:
                updates["chart_title"] = chart_info.get("title")
            if "subtitle" in chart_info:
                updates["chart_subtitle"] = chart_info.get("subtitle")
            if "x_label" in chart_info:
                updates["chart_x_label"] = chart_info.get("x_label")
            if "y_label" in chart_info:
                updates["chart_y_label"] = chart_info.get("y_label")
            if "note" in chart_info:
                updates["chart_note"] = chart_info.get("note")
            if "data_points" in chart_info:
                updates["chart_series"] = chart_info.get("data_points")
        still_info = metadata.get("still_motion") or {}
        if still_info:
            if "focus" in still_info:
                updates["still_focus"] = still_info.get("focus")
            if "reason" in still_info:
                updates["still_reason"] = still_info.get("reason")
        if not updates:
            return visual
        return visual.model_copy(update=updates)

    def _ensure_visual_defaults(
        self,
        visual: BeatVisualSpec,
        metadata: dict[str, Any] | None = None,
    ) -> BeatVisualSpec:
        if visual.type and visual.type.lower() == "chart" and not visual.spec_id:
            variant = None
            if metadata:
                variant = (metadata.get("chart") or {}).get("variant")
            spec_id = self._spec_id_for_variant(variant)
            if spec_id:
                return visual.model_copy(update={"spec_id": spec_id})
        return visual

    def _default_chart_spec_id(self) -> str | None:
        if self.style_template and self.style_template.chart_specs:
            for key in self.style_template.chart_specs:
                return key
        return None

    def _spec_id_for_variant(self, variant: str | None) -> str | None:
        if self.style_template and self.style_template.chart_specs:
            if variant and variant in self.style_template.chart_specs:
                return variant
            if variant:
                normalized = variant.lower()
                for key in self.style_template.chart_specs:
                    if normalized in key.lower():
                        return key
        if not variant:
            return self._default_chart_spec_id()
        lookup = {
            "donut": "sample_donut",
            "pie": "sample_donut",
            "bar": "sample_bar",
            "column": "sample_bar",
            "line": "sample_line",
            "trend": "sample_line",
            "area": "sample_line",
        }
        spec_id = lookup.get(variant.lower())
        if spec_id and self.style_template and spec_id in self.style_template.chart_specs:
            return spec_id
        return self._default_chart_spec_id()

    def _build_dynamic_chart_specs(self, chart_plan: ChartPlan) -> dict[str, ChartSpec]:
        specs: dict[str, ChartSpec] = {}
        for chart in chart_plan.charts:
            try:
                specs[chart.id] = self._chart_spec_from_idea(chart)
            except Exception as exc:  # pragma: no cover - guard against malformed chart data
                logger.warning("Unable to build chart spec for %s: %s", chart.id, exc)
        return specs

    def _chart_spec_from_idea(self, chart: ChartIdea) -> ChartSpec:
        values = [
            {
                "label": point.label,
                "value": point.value,
                "secondary_value": point.secondary_value,
                "series": point.series,
            }
            for point in chart.data_points
        ]
        return ChartSpec(
            library="aivideomaker.dynamic",
            width=1080,
            height=1920,
            data={
                "values": values,
                "note": chart.note or chart.summary,
                "source": chart.source,
            },
            mark=self._chart_mark_from_variant(chart.variant),
            encoding={},
            style=None,
        )

    def _chart_mark_from_variant(self, variant: str | None) -> str:
        if not variant:
            return "bar"
        normalized = variant.lower()
        if normalized in {"bar", "column"}:
            return "bar"
        if normalized in {"line", "trend"}:
            return "line"
        if normalized in {"pie", "donut", "arc"}:
            return "donut"
        if normalized in {"area"}:
            return "area"
        return normalized

    def _downgrade_unassigned_chart_beats(
        self,
        script: ScriptPlan,
        assignments: dict[str, str],
    ) -> ScriptPlan:
        updated_beats: list[Beat] = []
        assigned_ids = set(assignments.keys())
        for beat in script.beats:
            visual = beat.visual
            vtype = (visual.type or "") if visual else ""
            if beat.id in assigned_ids:
                updated_beats.append(beat)
                continue

            if not visual:
                updated_beats.append(beat)
                continue

            fallback = visual.model_copy(
                update={
                    "type": "cinematic_broll",
                    "spec_id": None,
                    "chart_variant": None,
                    "chart_reason": None,
                    "chart_data_available": None,
                    "chart_should_render": None,
                    "chart_duplicates_previous": None,
                    "chart_title": None,
                    "chart_subtitle": None,
                    "chart_x_label": None,
                    "chart_y_label": None,
                    "chart_note": None,
                    "chart_series": None,
                }
            )
            updated_beats.append(beat.model_copy(update={"visual": fallback}))
        return script.model_copy(update={"beats": updated_beats})

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

        chart_plan: ChartPlan | None = None
        chart_outline: str | None = None
        if self.chart_planner:
            try:
                chart_plan = self.chart_planner.analyze_article(article)
                if chart_plan and not chart_plan.is_empty():
                    chart_outline = "\n".join(chart_plan.summary_lines())
            except Exception as exc:  # pragma: no cover - defensive around LLM calls
                logger.warning("Chart analysis failed; continuing without pre-selected charts: %s", exc)
                chart_plan = ChartPlan(charts=[])

        if chart_plan is None:
            chart_plan = ChartPlan(charts=[])

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
                chart_outline=chart_outline,
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

        chart_assignments: dict[str, str] = {}
        if self.chart_assigner and chart_plan and not chart_plan.is_empty():
            try:
                script, assignments = self.chart_assigner.assign(chart_plan, script)
                chart_assignments = {assignment.beat_id: assignment.chart_id for assignment in assignments}
            except Exception as exc:  # pragma: no cover - defensive safeguard
                logger.warning("Failed to assign charts to beats: %s", exc)
                chart_assignments = {}

        if chart_assignments:
            script = self._downgrade_unassigned_chart_beats(script, chart_assignments)

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
        if chart_plan and chart_plan.charts:
            dynamic_specs = self._build_dynamic_chart_specs(chart_plan)
            for key, spec in dynamic_specs.items():
                chart_specs.setdefault(key, spec)
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
            chart_plan=chart_plan,
            chart_assignments=chart_assignments,
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

        existing_clip = self._existing_clip_path(run_dirs, clip_id)
        if existing_clip:
            logger.info("♻️  Reusing existing clip for %s at %s", clip_id, existing_clip)
            updated_bundle = self._bundle_with_asset(bundle, clip_id, existing_clip, run_dirs)
            return ClipRenderResult(bundle=updated_bundle, clip_id=clip_id, clip_asset=existing_clip)

        clip_path: Optional[Path] = None
        
        # Check render_mode from prompt FIRST (overrides beat visual type)
        # This ensures first chunk is always Sora even if beat says "still_motion" or "chart"
        if prompt.render_mode == "sora_clip":
            logger.debug("Clip %s has render_mode=sora_clip, forcing Sora generation", clip_id)
            # Skip chart/still generation, go straight to Sora/Veo
        else:
            # Otherwise, check beat's visual type for chart/still
            visual_mode = self._resolve_visual_mode(beat)
            
            if visual_mode == "chart":
                clip_path = self._render_chart_clip(bundle, beat, run_dirs, clip_id, prompt)
            if clip_path is None and visual_mode in {"still_motion", "still"}:
                clip_path = self._render_still_clip(prompt, run_dirs, clip_id, beat)

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

        updated_bundle = self._bundle_with_asset(bundle, clip_id, clip_path, run_dirs)
        return ClipRenderResult(bundle=updated_bundle, clip_id=clip_id, clip_asset=clip_path)

    def _bundle_with_asset(
        self,
        bundle: PipelineBundle,
        clip_id: str,
        clip_path: Path,
        run_dirs: dict[str, Path],
    ) -> PipelineBundle:
        try:
            stored_asset = clip_path.relative_to(run_dirs["run_dir"])
        except ValueError:
            stored_asset = clip_path

        existing_assets = [Path(asset) for asset in bundle.sora_assets]
        filtered_assets = [asset for asset in existing_assets if asset.stem != clip_id]
        updated_assets = filtered_assets + [stored_asset]
        return bundle.model_copy(update={"sora_assets": updated_assets})

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
        beat: Beat | None,
    ) -> Path:
        if not self.still_image_client:
            raise RuntimeError("Still image client is not configured")
        if not self.still_scene_prompter:
            raise RuntimeError("Still scene prompter is not configured")
        scene_prompt = self.still_scene_prompter.build_still_scene_prompt(prompt, beat)
        final_prompt = scene_prompt.prompt
        use_vertex = bool(getattr(self.still_image_client, "use_vertex", False))
        negative_for_client: Optional[str] = None
        if prompt.negative_prompt:
            final_prompt = (
                f"{final_prompt}\nAvoid the following visual elements: {prompt.negative_prompt}."
            )
            if not use_vertex:
                negative_for_client = prompt.negative_prompt
        negative_for_client = (
            negative_for_client if negative_for_client is not None else None
        )
        self._log_scene_prompt(
            run_dirs["stills_dir"],
            clip_id,
            final_prompt,
            negative_for_client,
            scene_type="still_scene",
            extra=scene_prompt.metadata,
        )
        image_path = self.still_image_client.generate(
            final_prompt,
            negative_for_client,
            clip_id,
            aspect_ratio="9:16",
        )
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
        if not beat or not beat.visual:
            return None

        charts_dir = run_dirs["charts_dir"]
        chart_png: Optional[Path] = None

        if self.openai_chart_client:
            try:
                spec = build_chart_codegen_spec(beat)
                chart_png = self.openai_chart_client.generate_chart(spec, charts_dir, clip_id)
                logger.info("🧑‍🎨  OpenAI generated chart for %s at %s", clip_id, chart_png)
            except Exception as exc:
                logger.warning("OpenAI chart generation failed for %s: %s", clip_id, exc)
                chart_png = None

        if chart_png is None:
            if not self.chart_renderer or not beat.visual.spec_id:
                return None
            spec = bundle.chart_specs.get(beat.visual.spec_id)
            if not spec:
                logger.warning("No chart spec '%s' found; falling back to still generation", beat.visual.spec_id)
                return None
            chart_png = Path(self.chart_renderer.render(spec, clip_id))

        scene_path = self._compose_chart_scene(chart_png, beat, run_dirs, clip_id, prompt)
        if scene_path is None:
            # Absolute fallback: use the raw chart image
            scene_path = chart_png

        duration = max(float(prompt.duration_sec or 3.0), 2.0)
        return self._image_to_video(Path(scene_path), run_dirs["sora_dir"], clip_id, duration)

    def _compose_chart_scene(
        self,
        chart_image: Path,
        beat: Beat | None,
        run_dirs: dict[str, Path],
        clip_id: str,
        prompt: MediaPrompt,
    ) -> Path:
        stills_dir = run_dirs["stills_dir"]
        stills_dir.mkdir(parents=True, exist_ok=True)
        target = stills_dir / f"{clip_id}.png"

        if self.still_image_client and self.still_scene_prompter:
            scene_prompt = self.still_scene_prompter.build_chart_scene_prompt(
                prompt,
                beat,
                chart_image,
            )
            final_prompt = scene_prompt.prompt
            use_vertex = bool(getattr(self.still_image_client, "use_vertex", False))
            negative_for_client: Optional[str] = None
            if prompt.negative_prompt:
                final_prompt = (
                    f"{final_prompt}\nAvoid the following visual elements: {prompt.negative_prompt}."
                )
                if not use_vertex:
                    negative_for_client = prompt.negative_prompt
            self._log_scene_prompt(
                stills_dir,
                clip_id,
                final_prompt,
                negative_for_client,
                scene_type="chart_scene",
                extra=scene_prompt.metadata,
            )
            try:
                generated_path = Path(
                    self.still_image_client.generate(
                        prompt=final_prompt,
                        negative=negative_for_client,
                        output_name=clip_id,
                        image_prompts=[chart_image],
                        aspect_ratio="9:16",
                    )
                )
                if not generated_path.is_absolute():
                    generated_path = stills_dir / generated_path
                if generated_path.exists():
                    if generated_path != target:
                        shutil.copyfile(generated_path, target)
                    else:
                        target = generated_path
                    return target
            except Exception as exc:  # pragma: no cover - defensive around external service
                logger.warning("Still image client failed to compose chart scene: %s", exc)

        return self._compose_chart_scene_stub(chart_image, target, beat)

    def _compose_chart_scene_stub(self, chart_image: Path, target: Path, beat: Beat | None) -> Path:
        self._log_scene_prompt(
            target.parent,
            target.stem,
            "Fallback chart scene stub prompt",
            None,
            scene_type="chart_scene_stub",
            extra={"chart_image": str(chart_image)},
        )
        try:
            chart = Image.open(chart_image).convert("RGBA")
        except Exception:
            chart = None

        scene = Image.new("RGBA", (1080, 1920), (26, 34, 48, 255))
        draw = ImageDraw.Draw(scene)

        # Create a simple workspace backdrop
        draw.rectangle([0, int(scene.height * 0.55), scene.width, scene.height], fill=(18, 22, 30, 255))
        draw.rectangle(
            [int(scene.width * 0.08), int(scene.height * 0.14), int(scene.width * 0.92), int(scene.height * 0.62)],
            fill=(12, 16, 24, 245),
            outline=(88, 104, 128, 255),
            width=4,
        )

        if chart is not None:
            monitor_width = int(scene.width * 0.78)
            monitor_height = int(scene.height * 0.38)
            chart_resized = chart.resize((monitor_width, monitor_height), Image.LANCZOS)
            paste_x = (scene.width - monitor_width) // 2
            paste_y = int(scene.height * 0.18)
            scene.alpha_composite(chart_resized, dest=(paste_x, paste_y))

        title_text = beat.purpose if beat else "Data Insight"
        font_title = self._load_font(48)
        draw.text((scene.width // 2, int(scene.height * 0.64)), title_text, font=font_title, fill=(230, 235, 242, 255), anchor="ma")

        final = scene.convert("RGB")
        final.save(target, format="PNG")
        return target

    def _log_scene_prompt(
        self,
        stills_dir: Path,
        clip_id: str,
        prompt_text: str,
        negative_prompt: Optional[str],
        *,
        scene_type: str,
        extra: Optional[dict[str, Any]] = None,
    ) -> None:
        try:
            prompts_dir = stills_dir / "_prompts"
            prompts_dir.mkdir(parents=True, exist_ok=True)
            payload: dict[str, Any] = {
                "scene_type": scene_type,
                "prompt": prompt_text,
                "negative_prompt": negative_prompt,
            }
            if extra:
                payload.update(extra)
            (prompts_dir / f"{clip_id}.json").write_text(
                json.dumps(payload, indent=2),
                encoding="utf-8",
            )
        except Exception as exc:  # pragma: no cover - logging is best-effort
            logger.debug("Failed to log scene prompt for %s: %s", clip_id, exc)

    def _load_font(self, size: int) -> ImageFont.FreeTypeFont:
        try:
            return ImageFont.truetype("DejaVuSans.ttf", size=size)
        except Exception:  # pragma: no cover
            return ImageFont.load_default()

    def _prepare_frame_for_video(self, image_path: Path) -> Path:
        """Return path to a 1080x1920 RGB frame suitable for encoding."""
        try:
            with Image.open(image_path) as img:
                img = img.convert("RGB")
                original_size = img.size
                target_size = (1080, 1920)
                fitted = ImageOps.fit(img, target_size, method=Image.LANCZOS, centering=(0.5, 0.5))
        except Exception as exc:  # pragma: no cover
            logger.warning("Failed to prepare frame %s: %s", image_path, exc)
            return image_path

        if original_size == target_size and image_path.suffix.lower() in {".png", ".jpg", ".jpeg"}:
            return image_path

        temp_path = image_path.with_suffix(".prepared.png")
        fitted.save(temp_path, format="PNG")
        return temp_path

    @staticmethod
    def _clamp01(value: float) -> float:
        if value < 0.0:
            return 0.0
        if value > 1.0:
            return 1.0
        return value

    @classmethod
    def _kenburns_duration_scale(cls, duration: float) -> float:
        if duration <= 1.5:
            return 0.35
        if duration >= 5.0:
            return 1.0
        span = 5.0 - 1.5
        return 0.35 + ((duration - 1.5) / span) * (1.0 - 0.35)

    @classmethod
    def _kenburns_progress(
        cls,
        t: float,
        total: float,
        *,
        hold_start: float = 0.0,
        hold_end: float = 0.0,
    ) -> float:
        if total <= 0:
            return 1.0
        normalized = cls._clamp01(t / total)
        hold_start = cls._clamp01(hold_start)
        hold_end = cls._clamp01(hold_end)
        hold_total = hold_start + hold_end
        if hold_total >= 0.98:
            return 1.0 if normalized >= 0.5 else 0.0
        active_span = 1.0 - hold_total
        if normalized <= hold_start:
            return 0.0
        if normalized >= 1.0 - hold_end:
            return 1.0
        return (normalized - hold_start) / active_span

    @staticmethod
    def _kenburns_apply_ease(value: float, easing: str | None) -> float:
        value = PipelineOrchestrator._clamp01(value)
        if not easing:
            return value
        key = easing.lower()
        if key in {"linear", "none"}:
            return value
        if key in {"ease_in", "ease_in_quad"}:
            return value * value
        if key in {"ease_out", "ease_out_quad"}:
            inv = 1.0 - value
            return 1.0 - inv * inv
        if key in {"ease_in_cubic"}:
            return value * value * value
        if key in {"ease_out_cubic"}:
            inv = 1.0 - value
            return 1.0 - inv * inv * inv
        if key in {"ease_in_out", "smoothstep"}:
            return value * value * (3.0 - 2.0 * value)
        return value

    @classmethod
    def _kenburns_motion_spec(
        cls,
        pattern: dict[str, Any],
        duration: float,
    ) -> dict[str, Any]:
        start_scale = float(pattern.get("start_scale", 1.0))
        end_scale_raw = float(pattern.get("end_scale", 1.0))

        start_center_raw = pattern.get("start_center", (0.5, 0.5))
        end_center_raw = pattern.get("end_center", start_center_raw)
        start_center = (
            cls._clamp01(float(start_center_raw[0])),
            cls._clamp01(float(start_center_raw[1])),
        )
        end_center_target = (
            cls._clamp01(float(end_center_raw[0])),
            cls._clamp01(float(end_center_raw[1])),
        )

        hold_cfg = pattern.get("hold") or {}
        hold_start = cls._clamp01(float(hold_cfg.get("start", 0.0)))
        hold_end = cls._clamp01(float(hold_cfg.get("end", 0.0)))

        duration_scale = cls._kenburns_duration_scale(duration)

        scale_strength = float(pattern.get("scale_strength", pattern.get("motion_strength", 1.0)))
        pan_strength = float(pattern.get("pan_strength", pattern.get("motion_strength", 1.0)))
        scale_strength = cls._clamp01(scale_strength * duration_scale)
        pan_strength = cls._clamp01(pan_strength * duration_scale)

        end_scale = start_scale + (end_scale_raw - start_scale) * scale_strength
        end_center = (
            cls._clamp01(start_center[0] + (end_center_target[0] - start_center[0]) * pan_strength),
            cls._clamp01(start_center[1] + (end_center_target[1] - start_center[1]) * pan_strength),
        )

        return {
            "start_scale": start_scale,
            "end_scale": end_scale,
            "start_center": start_center,
            "end_center": end_center,
            "hold_start": hold_start,
            "hold_end": hold_end,
            "scale_ease": pattern.get("scale_ease") or pattern.get("ease") or "smoothstep",
            "pan_ease": pattern.get("pan_ease") or pattern.get("ease") or "smoothstep",
        }

    def _build_kenburns_clip(self, image_path: Path, duration: float) -> CompositeVideoClip:
        pattern = self._KENBURNS_PATTERNS[self._kenburns_index % len(self._KENBURNS_PATTERNS)]
        self._kenburns_index += 1

        base_clip = ImageClip(str(image_path))
        frame_w, frame_h = base_clip.size
        total = max(duration, 0.01)

        motion = self._kenburns_motion_spec(pattern, total)

        start_scale = motion["start_scale"]
        end_scale = motion["end_scale"]
        start_center = motion["start_center"]
        end_center = motion["end_center"]
        hold_start = motion["hold_start"]
        hold_end = motion["hold_end"]
        scale_ease = motion["scale_ease"]
        pan_ease = motion["pan_ease"]

        start_center_px = (start_center[0] * frame_w, start_center[1] * frame_h)
        end_center_px = (end_center[0] * frame_w, end_center[1] * frame_h)

        def scale_progress(t: float) -> float:
            base = self._kenburns_progress(t, total, hold_start=hold_start, hold_end=hold_end)
            return self._kenburns_apply_ease(base, scale_ease)

        def pan_progress(t: float) -> float:
            base = self._kenburns_progress(t, total, hold_start=hold_start, hold_end=hold_end)
            return self._kenburns_apply_ease(base, pan_ease)

        def scale_func(t: float) -> float:
            p = scale_progress(t)
            return start_scale + (end_scale - start_scale) * p

        def center_func(t: float) -> tuple[float, float]:
            p = pan_progress(t)
            cx = start_center_px[0] + (end_center_px[0] - start_center_px[0]) * p
            cy = start_center_px[1] + (end_center_px[1] - start_center_px[1]) * p
            return cx, cy

        def position_func(t: float) -> tuple[float, float]:
            s = scale_func(t)
            cx, cy = center_func(t)
            w = frame_w * s
            h = frame_h * s
            return cx - w / 2.0, cy - h / 2.0

        animated = base_clip.resize(lambda t: scale_func(t)).set_position(position_func)
        composite = CompositeVideoClip([animated], size=(frame_w, frame_h)).set_duration(duration)
        return composite

    def _image_to_video(
        self,
        image_path: Path,
        target_dir: Path,
        clip_id: str,
        duration: float,
    ) -> Path:
        target_dir.mkdir(parents=True, exist_ok=True)
        output = target_dir / f"{clip_id}.mp4"
        prepared = self._prepare_frame_for_video(image_path)
        clip = self._build_kenburns_clip(prepared, duration).set_fps(30)
        clip.write_videofile(
            str(output),
            codec="libx264",
            audio=False,
            bitrate="4000k",
            logger=None,
        )
        clip.close()
        if prepared != image_path:
            try:
                prepared.unlink()
            except OSError:
                pass
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
        run_dir = (output_dir / slug).resolve()
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

    @staticmethod
    def _try_rel_path(path: Path, base: Path) -> Path | None:
        try:
            return path.relative_to(base)
        except ValueError:
            return None

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
        media_assets: list[Path] = []
        if prompts_only:
            logger.info("🚧  Prompts-only mode: skipping media submission")
        elif stitch_only:
            media_assets = self._collect_existing_assets(bundle, run_dirs["sora_dir"])
        else:
            pre_rendered: dict[str, Path] = {}
            sora_queue: list[tuple[str, "MediaPrompt"]] = []

            for prompt in prompts.media_prompts:
                clip_id = prompt.chunk_id
                chunk_ref = next(
                    (c for c in bundle.chunks.chunks if getattr(c, "id", c.beat_id) == clip_id),
                    None,
                )
                beat_id = chunk_ref.beat_id if chunk_ref else clip_id
                beat = next((b for b in bundle.script.beats if b.id == beat_id), None)
                
                # Check render_mode from prompt FIRST (this overrides beat visual type)
                # This ensures first chunk is always Sora even if beat says "still_motion"
                if prompt.render_mode == "sora_clip":
                    logger.debug("Clip %s has render_mode=sora_clip, forcing Sora generation", clip_id)
                    sora_queue.append((clip_id, prompt))
                    continue
                
                # Otherwise, use beat's visual type
                visual_mode = self._resolve_visual_mode(beat)

                clip_path: Path | None = None
                if visual_mode == "chart":
                    clip_path = self._render_chart_clip(bundle, beat, run_dirs, clip_id, prompt)
                    if clip_path:
                        logger.info("📊  Generated chart clip for %s at %s", clip_id, clip_path)
                        pre_rendered[clip_id] = clip_path
                        continue
                elif visual_mode in {"still_motion", "still"}:
                    clip_path = self._render_still_clip(prompt, run_dirs, clip_id, beat)
                    if clip_path:
                        logger.info("🖼️  Generated still clip for %s at %s", clip_id, clip_path)
                        pre_rendered[clip_id] = clip_path
                        continue

                # Only reach here if no chart/still was generated - add to Sora queue
                sora_queue.append((clip_id, prompt))

            if sora_queue:
                queue_prompts = [item[1] for item in sora_queue]
                if provider == "sora":
                    real_sora = not dry_run
                    if real_sora and not getattr(self.media_client, "api_key", None):
                        raise RuntimeError(
                            f"Missing Sora API key. Set {self.config.sora_api_key_env} in your environment."
                        )
                    submit_dry_run = not real_sora
                    logger.info(
                        "🎬  Submitting %d prompts to Sora (dry_run=%s)",
                        len(queue_prompts),
                        submit_dry_run,
                    )
                    sora_assets = self.media_client.submit_prompts(queue_prompts, dry_run=submit_dry_run)
                elif provider == "veo":
                    if dry_run:
                        logger.info("🧪  Dry run: skipping Veo submission for %d prompts", len(queue_prompts))
                        sora_assets = self.media_client.submit_prompts(queue_prompts, dry_run=True)
                    else:
                        has_key = bool(getattr(self.media_client, "api_key", None))
                        uses_vertex = bool(getattr(self.media_client, "use_vertex", False))
                        if not has_key and not uses_vertex:
                            raise RuntimeError(
                                f"Missing Veo API key. Set {self.config.veo_api_key_env} in your environment."
                            )
                        logger.info("🎬  Submitting %d prompts to Veo model %s", len(queue_prompts), self.config.veo_model)
                        sora_assets = self.media_client.submit_prompts(queue_prompts, dry_run=False)
                else:
                    raise ValueError(f"Unsupported media_provider '{self.config.media_provider}'")

                for (clip_id, _), asset_path in zip(sora_queue, sora_assets, strict=True):
                    pre_rendered.setdefault(clip_id, Path(asset_path))

            for prompt in prompts.media_prompts:
                clip_path = pre_rendered.get(prompt.chunk_id)
                if not clip_path:
                    raise RuntimeError(f"No asset generated for clip {prompt.chunk_id}")
                media_assets.append(clip_path)

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

        final_video_path: Path | None = None
        if final_video:
            candidate = Path(final_video)
            if candidate.is_absolute():
                final_video_path = self._try_rel_path(candidate, run_dirs["run_dir"]) or candidate
            else:
                final_video_path = candidate

        captions_ass_store: Path | None = None
        if captions_ass_path:
            candidate = Path(captions_ass_path)
            if candidate.is_absolute():
                captions_ass_store = self._try_rel_path(candidate, run_dirs["run_dir"]) or candidate
            else:
                captions_ass_store = candidate

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
                "captions_ass_path": captions_ass_store or bundle.captions_ass_path,
                "final_video": final_video_path or final_video,
            }
        )
