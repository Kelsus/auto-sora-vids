from __future__ import annotations

from typing import Iterable, Optional

from aivideomaker.article_ingest.model import ArticleBundle
from aivideomaker.chunker.model import ChunkPlan
from aivideomaker.script_engine.model import Beat, BeatQCRules, BeatVisualSpec, ScriptPlan

from .model import MediaPrompt, MediaPromptBundle, VoiceDirective


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
            voice_directive = (
                VoiceDirective(voice_id=self.default_voice)
                if self.default_voice
                else None
            )
            media_prompts.append(
                MediaPrompt(
                    chunk_id=getattr(chunk, "id", chunk.beat_id),
                    transcript=chunk.transcript,
                    visual_prompt=visual_prompt,
                    audio_prompt=audio_prompt,
                    duration_sec=chunk.estimated_duration_sec,
                    negative_prompt=negative_prompt,
                    cameo_voice=voice_directive,
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

        shot_instructions: list[str] = []
        if visual_spec:
            shot_instructions.extend(self._visual_type_instructions(visual_spec))
            if visual_spec.macro:
                shot_instructions.append(visual_spec.macro)

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
