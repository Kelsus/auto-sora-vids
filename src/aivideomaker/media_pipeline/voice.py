from __future__ import annotations

import logging
from pathlib import Path

from aivideomaker.prompt_builder.model import VoiceDirective

logger = logging.getLogger(__name__)


class VoiceSessionManager:
    """Tracks cameo voice assets to ensure consistent narration across clips."""

    def __init__(self, base_dir: Path) -> None:
        self.base_dir = base_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def prepare_voice(self, directive: VoiceDirective, script_text: str, dry_run: bool = True) -> Path:
        voice_dir = self.base_dir / directive.voice_id
        voice_dir.mkdir(parents=True, exist_ok=True)
        transcript_path = voice_dir / "transcript.txt"
        transcript_path.write_text(script_text, encoding="utf-8")
        if dry_run:
            logger.info("Dry run: capturing transcript for voice %s", directive.voice_id)
        else:
            raise NotImplementedError("Integrate with cameo voice capture API")
        return transcript_path
