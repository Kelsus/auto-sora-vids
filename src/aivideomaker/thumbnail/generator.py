from __future__ import annotations

import base64
import io
import logging
import textwrap
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

from aivideomaker.script_engine.llm import LLMClient
from aivideomaker.script_engine.model import ScriptPlan

logger = logging.getLogger(__name__)

_THUMBNAIL_WIDTH = 1080
_THUMBNAIL_HEIGHT = 1920
_FONT_SIZE = 80
_MAX_CHARS_PER_LINE = 18
_STROKE_WIDTH = 4
_GRADIENT_RATIO = 0.40

_TITLE_SYSTEM_PROMPT = (
    "You write short, punchy thumbnail titles for social media videos. "
    "Output ONLY the title text — no quotes, no explanation, no hashtags. "
    "Make it curiosity-driven and attention-grabbing."
)

_TITLE_USER_PROMPT = (
    "Write a 3-8 word viral thumbnail title for this video premise:\n\n{premise}\n\n"
    "Rules:\n"
    "- Exactly 3 to 8 words\n"
    "- ALL CAPS\n"
    "- No punctuation except ? or !\n"
    "- Must trigger curiosity\n"
    "- Output ONLY the title, nothing else"
)

_THUMBNAIL_DIRECTION_SYSTEM_PROMPT = (
    "You are a creative director for social media video thumbnails. You design "
    "unique, eye-catching thumbnail concepts that vary in expression, outfit, props, "
    "and composition. Output valid JSON only — no explanation, no markdown."
)

_THUMBNAIL_DIRECTION_USER_PROMPT = (
    "Design a unique thumbnail concept for this video premise:\n\n"
    "Premise: {premise}\n\n"
    "Return a JSON object with these fields:\n"
    '{{"expression": "...", "outfit_and_props": "...", "scene_and_framing": "..."}}\n\n'
    "Rules for each field:\n\n"
    "expression (5-15 words):\n"
    "- Match the story's emotional tone but pick a SPECIFIC nuanced expression.\n"
    "- Go beyond shock and horror. Use the full emotional spectrum: wry smirk, "
    "skeptical raised eyebrow, deadpan stare, exasperated eye-roll, knowing side-eye, "
    "nervous lip bite, sarcastic half-smile, quiet disappointment, steely determination, "
    "amused disbelief, uneasy forced grin, contemplative frown, mischievous grin, etc.\n"
    "- Irony works great: a calm sip of coffee while chaos unfolds, a thumbs-up with "
    "a pained smile, an eye-roll at absurd news.\n"
    "- Reserve wide-open mouth shock ONLY for genuinely jaw-dropping stories. Most "
    "stories call for subtler, more relatable expressions.\n"
    "- Happy/smiling is fine for positive stories.\n\n"
    "outfit_and_props (10-25 words):\n"
    "- Dress the character in whatever clothes best represent the story's topic — "
    "anything goes as long as it's highly recognizable and on-theme.\n"
    "- Examples: military fatigues and dog tags for defense, scrubs and stethoscope "
    "for health, judge's robe and gavel for legal, police uniform for law enforcement, "
    "chef's apron for food, lab coat and goggles for science, football jersey for "
    "sports, hoodie and headphones for tech, high-vis vest for infrastructure, "
    "political sash or flag pin for politics, hazmat suit for environmental crisis.\n"
    "- Include 1-2 handheld props that reinforce the topic: weapon replica, medical "
    "chart, legal document, protest sign, tool, product from the story, etc.\n"
    "- Be specific to THIS story — generic or plain outfits are boring.\n\n"
    "scene_and_framing (10-25 words):\n"
    "- Describe a specific background and camera framing tied to the story's setting.\n"
    "- Vary framing: close-up face, medium shot waist-up, over-the-shoulder, slight "
    "low angle, Dutch angle, character off-center with relevant background in focus.\n"
    "- Background should relate to the topic: newsroom, warehouse, lab, city street, "
    "trading floor, kitchen, stadium, hospital corridor, server room, courtroom, etc.\n"
    "- Include lighting mood: warm, cool, dramatic side-light, soft natural, neon-lit.\n"
    "- NEVER use: rooftop skylines, golden hour, floor-to-ceiling windows.\n"
)

_SCENE_PROMPT = (
    "Generate a 9:16 portrait thumbnail image. "
    "The character should be prominently visible and the focal point. "
    "Facial expression: {expression}. "
    "Outfit and props: {outfit_and_props}. "
    "Scene and framing: {scene_and_framing}. "
    "Eye-catching, high contrast, suitable for social media. No text overlays."
)


class ThumbnailGenerator:
    """Generates eye-catching thumbnails using OpenAI gpt-image-1 with text overlay."""

    def __init__(
        self,
        llm: Optional[LLMClient] = None,
        openai_client: Optional[object] = None,
        character_image_paths: Optional[list[Path]] = None,
    ) -> None:
        self._llm = llm
        self._openai_client = openai_client
        self._character_image_paths = character_image_paths or []

    def generate(self, export_dir: Path, script: ScriptPlan) -> Path:
        title = self._generate_title(script)
        scene = self._generate_character_scene(script)
        thumbnail = self._compose_thumbnail(scene, title)

        export_dir.mkdir(parents=True, exist_ok=True)
        out_path = export_dir / "thumbnail.png"
        thumbnail.save(str(out_path), format="PNG")
        logger.info("Saved thumbnail to %s", out_path)
        return out_path

    def _generate_title(self, script: ScriptPlan) -> str:
        if self._llm is not None:
            try:
                result = self._llm.complete(
                    _TITLE_USER_PROMPT.format(premise=script.premise),
                    system=_TITLE_SYSTEM_PROMPT,
                    max_tokens=60,
                    temperature=0.8,
                )
                title = result.strip().strip('"').strip("'")
                if title:
                    return title.upper()
            except Exception:
                logger.warning("LLM title generation failed, using fallback", exc_info=True)

        # Fallback: truncate premise at word boundary
        premise = script.premise
        if len(premise) <= 40:
            return premise.upper()
        truncated = premise[:40].rsplit(" ", 1)[0]
        return truncated.upper()

    def _generate_thumbnail_direction(self, script: ScriptPlan) -> dict[str, str]:
        fallback = {
            "expression": "intense, dramatic expression matching the story's emotional tone",
            "outfit_and_props": "professional attire appropriate to the topic",
            "scene_and_framing": "medium shot, well-lit environment related to the story",
        }
        if self._llm is not None:
            try:
                import json as _json

                result = self._llm.complete(
                    _THUMBNAIL_DIRECTION_USER_PROMPT.format(premise=script.premise),
                    system=_THUMBNAIL_DIRECTION_SYSTEM_PROMPT,
                    max_tokens=200,
                    temperature=0.9,
                )
                cleaned = result.strip()
                # Strip markdown fences if present
                if cleaned.startswith("```"):
                    cleaned = cleaned.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
                direction = _json.loads(cleaned)
                for key in fallback:
                    if not direction.get(key):
                        direction[key] = fallback[key]
                logger.info("Thumbnail direction: %s", direction)
                return direction
            except Exception:
                logger.warning("LLM thumbnail direction failed, using fallback", exc_info=True)
        return fallback

    def _generate_character_scene(self, script: ScriptPlan) -> Image.Image:
        if not self._openai_client:
            raise RuntimeError("OpenAI client is required for thumbnail generation")

        direction = self._generate_thumbnail_direction(script)
        prompt = _SCENE_PROMPT.format(
            expression=direction["expression"],
            outfit_and_props=direction["outfit_and_props"],
            scene_and_framing=direction["scene_and_framing"],
        )

        # Use images.edit when character references exist, images.generate otherwise
        image_inputs: list[object] = []
        for img_path in self._character_image_paths:
            if img_path.exists():
                image_inputs.append(open(img_path, "rb"))  # noqa: SIM115

        try:
            if image_inputs:
                response = self._openai_client.images.edit(
                    model="gpt-image-1",
                    image=image_inputs,
                    prompt=prompt,
                    size="1024x1536",
                    n=1,
                )
            else:
                response = self._openai_client.images.generate(
                    model="gpt-image-1",
                    prompt=prompt,
                    size="1024x1536",
                    n=1,
                )
        finally:
            for f in image_inputs:
                f.close()

        image_data = response.data[0]
        if image_data.b64_json:
            image_bytes = base64.b64decode(image_data.b64_json)
        elif image_data.url:
            import urllib.request
            with urllib.request.urlopen(image_data.url) as resp:
                image_bytes = resp.read()
        else:
            raise RuntimeError("No image data in OpenAI response")

        return Image.open(io.BytesIO(image_bytes))

    def _compose_thumbnail(self, frame: Image.Image, title: str) -> Image.Image:
        # Resize to cover target dimensions, then center-crop to avoid stretching
        src_w, src_h = frame.size
        scale = max(_THUMBNAIL_WIDTH / src_w, _THUMBNAIL_HEIGHT / src_h)
        new_w = int(src_w * scale)
        new_h = int(src_h * scale)
        img = frame.resize((new_w, new_h), Image.LANCZOS)
        left = (new_w - _THUMBNAIL_WIDTH) // 2
        top = (new_h - _THUMBNAIL_HEIGHT) // 2
        img = img.crop((left, top, left + _THUMBNAIL_WIDTH, top + _THUMBNAIL_HEIGHT))
        img = img.convert("RGBA")

        # Create gradient overlay at the bottom for text readability
        gradient = Image.new("RGBA", (_THUMBNAIL_WIDTH, _THUMBNAIL_HEIGHT), (0, 0, 0, 0))
        gradient_draw = ImageDraw.Draw(gradient)
        gradient_height = int(_THUMBNAIL_HEIGHT * _GRADIENT_RATIO)
        gradient_start = _THUMBNAIL_HEIGHT - gradient_height
        for y in range(gradient_height):
            alpha = int(180 * (y / gradient_height))
            gradient_draw.line(
                [(0, gradient_start + y), (_THUMBNAIL_WIDTH, gradient_start + y)],
                fill=(0, 0, 0, alpha),
            )

        img = Image.alpha_composite(img, gradient)

        # Word-wrap and draw title text
        font = self._load_font(_FONT_SIZE)
        draw = ImageDraw.Draw(img)
        lines = textwrap.wrap(title, width=_MAX_CHARS_PER_LINE)

        # Calculate line dimensions
        line_bboxes = [draw.textbbox((0, 0), line, font=font) for line in lines]
        line_heights = [bb[3] - bb[1] for bb in line_bboxes]
        line_spacing = 12

        # Position text in the lower portion (~65-75% down)
        total_text_height = sum(line_heights) + line_spacing * max(len(lines) - 1, 0)
        y_center = int(_THUMBNAIL_HEIGHT * 0.72)
        y_start = y_center - total_text_height // 2
        y_cursor = y_start

        for i, line in enumerate(lines):
            bb = line_bboxes[i]
            text_width = bb[2] - bb[0]
            x = (_THUMBNAIL_WIDTH - text_width) // 2
            draw.text(
                (x, y_cursor),
                line,
                font=font,
                fill="white",
                stroke_width=_STROKE_WIDTH,
                stroke_fill="black",
            )
            y_cursor += line_heights[i] + line_spacing

        # Convert to RGB for PNG output
        return img.convert("RGB")

    @staticmethod
    def _load_font(size: int) -> ImageFont.FreeTypeFont:
        try:
            return ImageFont.truetype("DejaVuSans-Bold.ttf", size=size)
        except Exception:
            try:
                return ImageFont.truetype("DejaVuSans.ttf", size=size)
            except Exception:
                return ImageFont.load_default()
