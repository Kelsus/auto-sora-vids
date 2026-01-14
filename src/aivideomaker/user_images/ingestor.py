from __future__ import annotations

import base64
import logging
import mimetypes
import re
from pathlib import Path
from typing import Iterable, List

from aivideomaker.script_engine.llm import LLMClient
from aivideomaker.script_engine.utils import load_json_with_repair

from .models import UserImageAsset, UserImagePlan

logger = logging.getLogger(__name__)

_TOKEN_PATTERN = re.compile(r"[a-z0-9']+")


class UserImageIngestor:
    def __init__(self, llm: LLMClient | None) -> None:
        self.llm = llm

    def build_plan(self, assets: Iterable[UserImageAsset]) -> UserImagePlan:
        enriched: List[UserImageAsset] = []
        for asset in assets:
            try:
                enriched.append(self._enrich_one(asset))
            except Exception as exc:  # pragma: no cover - defensive around IO/LLM
                logger.warning("Failed to analyze user image %s: %s", asset.materialized_path, exc)
                enriched.append(asset)
        return UserImagePlan(images=enriched)

    def _enrich_one(self, asset: UserImageAsset) -> UserImageAsset:
        path = Path(asset.materialized_path)
        if not path.is_absolute():
            # Caller is expected to materialize into the run directory; if they passed a
            # relative path we can still read it from the current working directory.
            path = Path.cwd() / path

        title, summary, keywords = self._analyze_image(path)
        update: dict = {}
        if title:
            update["title"] = title
        if summary:
            update["summary"] = summary
        if keywords:
            update["keywords"] = keywords
        return asset.model_copy(update=update)

    def _analyze_image(self, path: Path) -> tuple[str | None, str | None, list[str]]:
        fallback_title = path.stem.replace("_", " ").replace("-", " ").strip() or "User image"
        fallback_keywords = self._filename_keywords(path)

        mime_type, _ = mimetypes.guess_type(path.name)
        if not mime_type or not mime_type.startswith("image/"):
            return fallback_title, None, fallback_keywords

        if not self.llm:
            return fallback_title, None, fallback_keywords

        try:
            with path.open("rb") as handle:
                data = base64.b64encode(handle.read()).decode("utf-8")
            prompt = (
                "Analyze this user-provided image for a video production pipeline.\n"
                "Return a JSON object with fields:\n"
                "- title: short descriptive title\n"
                "- summary: 1-2 sentence summary\n"
                "- keywords: 3-8 keywords\n"
                "Return only JSON."
            )
            raw = self.llm.complete_with_images(prompt, images=[(mime_type, data)])
            payload = load_json_with_repair(raw, logger=logger)
        except NotImplementedError:
            return fallback_title, None, fallback_keywords
        except Exception:
            return fallback_title, None, fallback_keywords

        if not isinstance(payload, dict):
            return fallback_title, None, fallback_keywords

        title = str(payload.get("title") or "").strip() or fallback_title
        summary = str(payload.get("summary") or "").strip() or None

        keywords_payload = payload.get("keywords") or []
        keywords: list[str] = []
        if isinstance(keywords_payload, list):
            for item in keywords_payload:
                token = str(item or "").strip()
                if token:
                    keywords.append(token)
        if not keywords:
            keywords = fallback_keywords
        return title, summary, keywords

    def _filename_keywords(self, path: Path) -> list[str]:
        tokens = {match.group(0) for match in _TOKEN_PATTERN.finditer(path.stem.lower())}
        return sorted({token for token in tokens if len(token) > 2})


__all__ = ["UserImageIngestor"]

