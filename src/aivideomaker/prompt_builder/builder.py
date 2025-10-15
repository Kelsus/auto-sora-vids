from __future__ import annotations

from aivideomaker.article_ingest.model import ArticleBundle
from aivideomaker.chunker.model import ChunkPlan
from aivideomaker.script_engine.model import ScriptPlan

from .model import MediaPrompt, MediaPromptBundle, VoiceDirective


class MediaPromptBuilder:
    def __init__(
        self,
        default_voice: str | None = None,
        negative_prompt: str | None = None,
    ) -> None:
        self.default_voice = default_voice
        self.negative_prompt = negative_prompt

    def build(self, article: ArticleBundle, script: ScriptPlan, chunks: ChunkPlan) -> MediaPromptBundle:
        beat_map = {beat.id: beat for beat in script.beats}
        media_prompts = []
        for chunk in chunks.chunks:
            beat = beat_map[chunk.beat_id]
            visual_prompt = self._visual_prompt(article, beat.purpose, beat.visual_seed)
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
                    negative_prompt=self.negative_prompt,
                    cameo_voice=voice_directive,
                )
            )
        return MediaPromptBundle(article_slug=article.article.metadata.slug, media_prompts=media_prompts)

    def _visual_prompt(self, article: ArticleBundle, purpose: str, seed: str | None) -> str:
        base = f"News story about {article.article.metadata.title}." if article.article.metadata.title else "Contemporary news setting."
        if seed:
            base += f" Focus on {seed}."
        base += " Vertical 9:16 frame, optimized for smartphone viewing."
        base += " Avoid any on-screen text or subtitles."
        if purpose.lower() == "hook":
            base += " Dramatic close-ups, high contrast lighting."
        elif purpose.lower() == "reveal":
            base += " Medium shots unveiling key context."
        else:
            base += " Steady pacing with contextual visuals."
        return base

    def _audio_prompt(self, beat) -> str:
        mood = beat.audio_mood or "tense minimalistic score"
        tension = "Increase tension" if beat.suspense_level >= 4 else "Maintain suspense"
        return f"{mood}, {tension}, ensure space for voiceover"
