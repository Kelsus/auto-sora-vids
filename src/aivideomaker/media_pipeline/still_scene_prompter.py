from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from aivideomaker.prompt_builder.model import MediaPrompt
from aivideomaker.script_engine.model import Beat


@dataclass
class ScenePrompt:
    prompt: str
    metadata: dict[str, Any]


class StillScenePrompter:
    """Builds prompts for Gemini/Vertex still-image scenes."""

    _STRIP_KEYWORDS = {
        "camera motion",
        "vertical 9:16",
        "single continuous shot",
        "observational cinematic b-roll",
        "gentle parallax",
        "slow push-in",
        "lens",
    }

    def build_chart_scene_prompt(
        self,
        media_prompt: MediaPrompt,
        beat: Optional[Beat],
        chart_path: Path,
    ) -> ScenePrompt:
        base = self._strip_camera_instructions(media_prompt.visual_prompt)
        context_notes: list[str] = []
        if beat and beat.visual and beat.visual.chart_reason:
            context_notes.append(beat.visual.chart_reason)
        if beat and beat.purpose:
            context_notes.append(beat.purpose)
        context_text = " ".join(context_notes).strip()

        instructions = [
            base,
            "Capture a production-quality documentary still that could sit inside the final film as a practical setup.",
            "Integrate the provided chart organically within the environment (on a monitor, projection, printout, or tabletop) so it feels physically present and correctly lit.",
            "Match perspective, lighting, and color so the chart belongs in the scene; do not float or paste it flat on top.",
            "Preserve the chart\'s data and labeling exactly. Adjust framing, props, and set dressing to support the story point without altering the figure itself.",
        ]
        if context_text:
            instructions.append(f"Context for the scene: {context_text}.")

        prompt_text = " ".join(part.strip() for part in instructions if part).strip()
        metadata = {
            "chart_image": str(chart_path),
        }
        if context_notes:
            metadata["visual_notes"] = context_notes
        return ScenePrompt(prompt=prompt_text, metadata=metadata)

    def build_still_scene_prompt(
        self,
        media_prompt: MediaPrompt,
        beat: Optional[Beat],
    ) -> ScenePrompt:
        base = self._strip_camera_instructions(media_prompt.visual_prompt)
        extras = [
            base,
            "Generate a single production-ready still frame with rich texture and depth that can support light parallax animation afterwards.",
            "Ensure the composition feels documentary and grounded rather than stylized or synthetic.",
        ]
        prompt_text = " ".join(part.strip() for part in extras if part).strip()
        metadata: dict[str, Any] = {}
        if beat and beat.visual_seed:
            metadata["visual_seed"] = beat.visual_seed
        return ScenePrompt(prompt=prompt_text, metadata=metadata)

    def _strip_camera_instructions(self, text: str) -> str:
        sentences = re.split(r"(?<=[.!?])\s+", text.strip())
        filtered: list[str] = []
        for sentence in sentences:
            lowered = sentence.lower()
            if any(keyword in lowered for keyword in self._STRIP_KEYWORDS):
                continue
            filtered.append(sentence)
        if not filtered:
            return text
        return " ".join(filtered)
