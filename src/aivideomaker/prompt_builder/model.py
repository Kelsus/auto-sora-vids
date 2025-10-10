from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel


class VoiceDirective(BaseModel):
    voice_id: str
    reference_path: Optional[str] = None
    notes: Optional[str] = None


class SoraPrompt(BaseModel):
    chunk_id: str
    transcript: str
    visual_prompt: str
    audio_prompt: str
    cameo_voice: Optional[VoiceDirective] = None


class PromptBundle(BaseModel):
    article_slug: str
    sora_prompts: List[SoraPrompt]
    voice_session: Optional[str] = None
