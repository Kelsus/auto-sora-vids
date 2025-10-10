from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable

from aivideomaker.prompt_builder.model import SoraPrompt

logger = logging.getLogger(__name__)


class SoraClient:
    """Placeholder client that would wrap Sora 2 API once available."""

    def __init__(self, asset_dir: Path) -> None:
        self.asset_dir = asset_dir
        self.asset_dir.mkdir(parents=True, exist_ok=True)

    def submit_prompts(self, prompts: Iterable[SoraPrompt], dry_run: bool = True) -> list[Path]:
        assets: list[Path] = []
        for prompt in prompts:
            if dry_run:
                dummy = self.asset_dir / f"{prompt.chunk_id}.mp4"
                logger.info("Dry run: would request Sora video for %s", prompt.chunk_id)
                dummy.touch()
                assets.append(dummy)
            else:
                raise NotImplementedError("Real Sora integration pending")
        return assets
