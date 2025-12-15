from __future__ import annotations

import base64
import logging
import mimetypes
from pathlib import Path
from typing import List

from aivideomaker.script_engine.llm import LLMClient
from aivideomaker.script_engine.utils import load_json_with_repair

from .models import ChartIdea, ChartPlan

logger = logging.getLogger(__name__)


class ImageIngestor:
    def __init__(self, llm: LLMClient) -> None:
        self.llm = llm

    def analyze_images(self, paths: List[Path]) -> ChartPlan:
        logger.info(f"DEBUG: Analyzing {len(paths)} images: {paths}")
        charts: List[ChartIdea] = []
        for index, path in enumerate(paths):
            try:
                idea = self._analyze_single_image(path, index)
                charts.append(idea)
            except Exception as exc:
                logger.error("Failed to analyze image %s: %s", path, exc)
                continue
        logger.info(f"DEBUG: Generated {len(charts)} charts")
        return ChartPlan(charts=charts)

    def _analyze_single_image(self, path: Path, index: int) -> ChartIdea:
        mime_type, _ = mimetypes.guess_type(path)
        if not mime_type or not mime_type.startswith("image/"):
            raise ValueError(f"Invalid mime type for {path}: {mime_type}")

        with path.open("rb") as f:
            data = base64.b64encode(f.read()).decode("utf-8")

        prompt = """
        Analyze this image and extract metadata for a video production pipeline.
        The image is likely a chart, graph, or infographic.
        
        Return a JSON object with the following fields:
        - title: A short, descriptive title for the image.
        - summary: A 1-2 sentence summary of what the image shows.
        - reason: A short explanation of why this image is relevant or interesting.
        - keywords: A list of 3-5 keywords related to the image content.
        
        Example JSON:
        {
            "title": "Global Temperature Rise",
            "summary": "A line graph showing the increase in global average temperature from 1880 to 2020.",
            "reason": "Illustrates the long-term warming trend.",
            "keywords": ["climate change", "temperature", "warming", "graph"]
        }
        """

        raw = self.llm.complete_with_images(prompt, images=[(mime_type, data)])
        payload = load_json_with_repair(raw, logger=logger)

        return ChartIdea(
            id=f"image_chart_{index}_{path.stem}",
            title=payload.get("title", "Untitled Image"),
            summary=payload.get("summary", "No summary provided"),
            reason=payload.get("reason", "User provided image"),
            keywords=payload.get("keywords", []),
            image_path=str(path.absolute()),
            variant="image",  # Special variant for user images
        )
