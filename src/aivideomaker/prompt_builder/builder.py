from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

from aivideomaker.article_ingest.model import ArticleBundle
from aivideomaker.chunker.model import ChunkPlan
from aivideomaker.script_engine.model import Beat, BeatQCRules, BeatVisualSpec, ScriptPlan

from .model import MediaPrompt, MediaPromptBundle, VoiceDirective


@dataclass(frozen=True)
class PromptPreset:
    preset_id: str
    visual_type: str
    template: str


PROMPT_PRESETS: dict[str, PromptPreset] = {
    "docu_tension_closeup": PromptPreset(
        preset_id="docu_tension_closeup",
        visual_type="cinematic_broll",
        template=(
            "Intimate handheld close-up inside {subject_context}, highlighting {focus_phrase}. "
            "Natural lighting, shallow depth of field, subtle film grain, observing without narration presence."
        ),
    ),
    "docu_environment_wide": PromptPreset(
        preset_id="docu_environment_wide",
        visual_type="cinematic_broll",
        template=(
            "Wide documentary frame capturing {subject_context} in context, steady slow push to reveal scale. "
            "Real-world textures, ambient motion, no staged actors."
        ),
    ),
    "docu_resolution_medium": PromptPreset(
        preset_id="docu_resolution_medium",
        visual_type="cinematic_broll",
        template=(
            "Medium documentary shot that resolves the tension around {focus_phrase}. "
            "Camera remains stable, single continuous move, grounded realism."
        ),
    ),
    "still_motion_default": PromptPreset(
        preset_id="still_motion_default",
        visual_type="still_motion",
        template=(
            "High-resolution archival still of {focus_phrase} with gentle parallax and depth layering. "
            "Soft lighting, micro dust motes, respect the original texture."
        ),
    ),
    "chart_metaphor_default": PromptPreset(
        preset_id="chart_metaphor_default",
        visual_type="chart",
        template=(
            "Abstract macro shot symbolizing data insight about {focus_phrase}: flowing particles, clean gradients, "
            "subtle glow, no literal charts or text."
        ),
    ),
}

TYPE_DEFAULT_PRESETS: dict[str, str] = {
    "cinematic_broll": "docu_environment_wide",
    "still_motion": "still_motion_default",
    "chart": "chart_metaphor_default",
}


class MediaPromptBuilder:
    def __init__(
        self,
        default_voice: str | None = None,
        negative_prompt: str | None = None,
        visual_style: Optional[dict] = None,
    ) -> None:
        self.default_voice = default_voice
        self.configured_negative_prompt = negative_prompt
        self.visual_style = visual_style or {}
        self.base_negations: list[str] = list(self.visual_style.get("bans", []))
        self.motion_defaults: list[str] = list(self.visual_style.get("motion", []))
        self.lens_hint: str | None = self.visual_style.get("lens")
        self.palette: str | None = self.visual_style.get("palette")

    def build(self, article: ArticleBundle, script: ScriptPlan, chunks: ChunkPlan) -> MediaPromptBundle:
        beat_map = {beat.id: beat for beat in script.beats}
        media_prompts = []
        for chunk in chunks.chunks:
            beat = beat_map[chunk.beat_id]
            visual_prompt = self._visual_prompt(article, beat, chunk.estimated_duration_sec)
            negative_prompt = self._negative_prompt(beat)
            audio_prompt = self._audio_prompt(beat)
            visual_prompt, negative_prompt = self._lint_prompt(beat, visual_prompt, negative_prompt)
            voice_directive = (
                VoiceDirective(voice_id=self.default_voice)
                if self.default_voice
                else None
            )
            visual_type = self._resolve_visual_type(beat)
            render_mode = self._render_mode(beat, visual_type)
            visual_spec = beat.visual
            media_prompts.append(
                MediaPrompt(
                    chunk_id=getattr(chunk, "id", chunk.beat_id),
                    transcript=chunk.transcript,
                    visual_prompt=visual_prompt,
                    audio_prompt=audio_prompt,
                    duration_sec=chunk.estimated_duration_sec,
                    negative_prompt=negative_prompt,
                    cameo_voice=voice_directive,
                    visual_type=visual_type,
                    render_mode=render_mode,
                    chart_spec_id=getattr(visual_spec, "spec_id", None) if visual_spec else None,
                    chart_variant=getattr(visual_spec, "chart_variant", None) if visual_spec else None,
                )
            )
        return MediaPromptBundle(article_slug=article.article.metadata.slug, media_prompts=media_prompts)

    # ------------------------------------------------------------------
    # Prompt assembly helpers
    # ------------------------------------------------------------------

    def _visual_prompt(self, article: ArticleBundle, beat: Beat, duration: float) -> str:
        parts: list[str] = []
        title = article.article.metadata.title
        if title:
            parts.append(f"Documentary-style coverage about {title}.")
        else:
            parts.append("Documentary-style news coverage.")

        if beat.visual_seed:
            parts.append(f"Focus on {beat.visual_seed.strip()}.")

        if beat.intent:
            parts.append(f"Mood/intent: {beat.intent}.")

        visual_spec: BeatVisualSpec | None = beat.visual
        qc: BeatQCRules | None = beat.qc

        preset_lines: list[str] = []
        preset = self._select_preset(beat)
        if preset:
            preset_lines.append(
                preset.template.format(
                    subject_context=self._subject_context(article),
                    focus_phrase=self._focus_phrase(beat),
                )
            )

        shot_instructions: list[str] = []
        if visual_spec:
            shot_instructions.extend(self._visual_type_instructions(visual_spec))
        shot_instructions.append("Vertical 9:16 frame, cinematic realism.")

        if self.lens_hint:
            shot_instructions.append(f"Use lensing akin to {self.lens_hint}.")
        if self.motion_defaults:
            motions = ", ".join(self.motion_defaults)
            shot_instructions.append(f"Camera motion: {motions}.")
        if self.palette:
            shot_instructions.append(f"Color palette: {self.palette}.")

        if qc and not qc.allow_split_screen:
            shot_instructions.append("Single continuous shot, no split screens or overlays.")
        if qc and not qc.allow_text:
            shot_instructions.append("Keep frame free of on-screen text or subtitles.")
        if qc and not qc.allow_numbers:
            shot_instructions.append("Do not display numbers, charts, or digits.")

        if duration < 1.5:
            shot_instructions.append("Extend action to sustain at least 1.5 seconds of unique motion.")

        parts.extend(preset_lines)
        parts.append(" ".join(shot_instructions))
        return " ".join(part.strip() for part in parts if part)

    def _audio_prompt(self, beat) -> str:
        mood = beat.audio_mood or "tense minimalistic score"
        tension = "Increase tension" if beat.suspense_level >= 4 else "Maintain suspense"
        return f"{mood}, {tension}, ensure space for voiceover"

    def _negative_prompt(self, beat: Beat) -> str | None:
        elements: list[str] = []
        if self.configured_negative_prompt:
            elements.append(self.configured_negative_prompt)

        self._extend_unique(elements, self.base_negations)

        visual_spec = beat.visual
        if visual_spec and visual_spec.negations:
            self._extend_unique(elements, visual_spec.negations)

        qc = beat.qc
        if qc:
            if not qc.allow_text:
                self._extend_unique(elements, ["text", "captions", "subtitles"])
            if not qc.allow_numbers:
                self._extend_unique(elements, ["digits", "numbers", "charts"])
            if not qc.allow_split_screen:
                self._extend_unique(elements, ["split screen", "side-by-side layout"])
        else:
            # Default to banning split screens when QC not specified.
            self._extend_unique(elements, ["split screen", "side-by-side layout"])

        if not elements:
            return None
        # Flatten nested comma-separated strings while preserving order.
        flattened: list[str] = []
        for item in elements:
            if not item:
                continue
            parts = [segment.strip() for segment in str(item).split(",") if segment.strip()]
            for part in parts:
                if part not in flattened:
                    flattened.append(part)
        return ", ".join(flattened) if flattened else None

    def _lint_prompt(
        self,
        beat: Beat,
        visual_prompt: str,
        negative_prompt: Optional[str],
    ) -> tuple[str, Optional[str]]:
        if "single continuous" not in visual_prompt.lower():
            visual_prompt = visual_prompt.strip() + " Single continuous shot, no split screens or overlays."
        if "vertical" not in visual_prompt.lower():
            visual_prompt = visual_prompt.strip() + " Vertical 9:16 composition."

        required_negatives = []
        qc = beat.qc
        if qc is None or not qc.allow_split_screen:
            required_negatives.extend(["split screen", "side-by-side layout"])
        if qc is None or not qc.allow_text:
            required_negatives.extend(["text", "captions", "subtitles"])
        if qc is None or not qc.allow_numbers:
            required_negatives.extend(["numbers", "digits", "charts"])

        current_negatives = []
        if negative_prompt:
            current_negatives = [part.strip() for part in negative_prompt.split(",") if part.strip()]
        self._extend_unique(current_negatives, required_negatives)

        if not current_negatives:
            negative_prompt = None
        else:
            negative_prompt = ", ".join(dict.fromkeys(current_negatives))
        return visual_prompt.strip(), negative_prompt

    def _resolve_visual_type(self, beat: Beat) -> str:
        visual_spec = beat.visual
        if visual_spec and visual_spec.type:
            return visual_spec.type.lower()
        return "cinematic_broll"

    def _render_mode(self, beat: Beat, visual_type: str) -> str:
        visual_spec = beat.visual
        if visual_type == "chart":
            should_render_chart = True
            if visual_spec and getattr(visual_spec, "chart_should_render", None) is not None:
                should_render_chart = bool(visual_spec.chart_should_render)
            return "chart_scene" if should_render_chart else "still_scene"
        if visual_type == "still_motion":
            return "still_scene"
        return "sora_clip"

    # ------------------------------------------------------------------
    # Internal utilities
    # ------------------------------------------------------------------

    def _visual_type_instructions(self, visual: BeatVisualSpec) -> Iterable[str]:
        vtype = (visual.type or "").lower()
        if vtype == "cinematic_broll":
            return [
                "Observational cinematic b-roll with grounded realism.",
                "Use natural lighting, shallow depth of field.",
            ]
        if vtype == "still_motion":
            return [
                "Start from a high-resolution still image and add gentle parallax/ken-burns motion.",
                "Keep motion smooth and purposeful, no rapid zooms.",
            ]
        if vtype == "chart":
            return [
                "Abstract visual metaphor for data insight; avoid literal charts or on-screen numbers.",
                "Use light particles or soft gradients to imply information flow.",
            ]
        return []

    @staticmethod
    def _extend_unique(target: list[str], items: Iterable[str]) -> None:
        seen = set(target)
        for item in items:
            normalized = item.strip()
            if not normalized or normalized in seen:
                continue
            target.append(normalized)
            seen.add(normalized)

    def _select_preset(self, beat: Beat) -> Optional[PromptPreset]:
        visual_spec = beat.visual
        if visual_spec and visual_spec.macro:
            preset = PROMPT_PRESETS.get(visual_spec.macro)
            if preset:
                return preset

        if visual_spec:
            default_id = TYPE_DEFAULT_PRESETS.get(visual_spec.type.lower())
            if default_id and default_id in PROMPT_PRESETS:
                return PROMPT_PRESETS[default_id]

        # Fallback to cinematic default if nothing else matches.
        return PROMPT_PRESETS.get(TYPE_DEFAULT_PRESETS.get("cinematic_broll", ""))

    def _subject_context(self, article: ArticleBundle) -> str:
        title = article.article.metadata.title
        if title:
            return title
        source = article.article.metadata.source or "the story"
        return source

    def _focus_phrase(self, beat: Beat) -> str:
        if beat.visual_seed:
            return beat.visual_seed
        transcript = beat.transcript.strip()
        if not transcript:
            return "the topic"
        sentence = transcript.split(".")[0]
        return sentence[:140]
