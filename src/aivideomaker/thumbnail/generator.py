from __future__ import annotations

import base64
import io
import logging
import textwrap
import time
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

_EMOTION_SYSTEM_PROMPT = (
    "You are an expert at reading the emotional tone of news stories and translating "
    "that into vivid facial expression directions for thumbnail photography. "
    "Output ONLY a short description of the facial expression — no explanation."
)

_EMOTION_USER_PROMPT = (
    "Based on this video premise, describe the ideal facial expression for a "
    "social media thumbnail character. The expression should match the emotional "
    "tone of the story but be EXAGGERATED for maximum impact and curiosity on "
    "social media (think YouTube thumbnails).\n\n"
    "Premise: {premise}\n\n"
    "Rules:\n"
    "- 5-20 words describing ONLY the facial expression\n"
    "- Match the story's emotion: shock, anger, grief, fear, disgust, worry, etc.\n"
    "- Exaggerate the expression — dramatic, intense, attention-grabbing\n"
    "- NEVER use smiling or happy expressions for serious/tragic/dark topics\n"
    "- Output ONLY the expression description, nothing else"
)

_SCENE_PROMPT = (
    "Generate a 9:16 portrait thumbnail image featuring this character. "
    "The character should be prominently visible. "
    "Facial expression: {expression}. "
    "Scene context: {premise}. "
    "Eye-catching, suitable for social media. No text overlays."
)


class ThumbnailGenerator:
    """Generates eye-catching thumbnails using Gemini character image generation with text overlay."""

    def __init__(
        self,
        llm: Optional[LLMClient] = None,
        gemini_client: Optional[object] = None,
        character_image_paths: Optional[list[Path]] = None,
    ) -> None:
        self._llm = llm
        self._gemini_client = gemini_client
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

    def _generate_expression(self, script: ScriptPlan) -> str:
        if self._llm is not None:
            try:
                result = self._llm.complete(
                    _EMOTION_USER_PROMPT.format(premise=script.premise),
                    system=_EMOTION_SYSTEM_PROMPT,
                    max_tokens=60,
                    temperature=0.7,
                )
                expression = result.strip().strip('"').strip("'")
                if expression:
                    logger.info("Thumbnail expression: %s", expression)
                    return expression
            except Exception:
                logger.warning("LLM expression generation failed, using fallback", exc_info=True)
        return "intense, dramatic expression matching the story's emotional tone"

    def _generate_character_scene(self, script: ScriptPlan) -> Image.Image:
        from google.genai import types
        from aivideomaker.media_pipeline.gemini_image_client import GeminiImageError

        if not self._gemini_client:
            raise GeminiImageError("Gemini client is required for thumbnail generation")

        expression = self._generate_expression(script)

        parts: list[object] = []
        for img_path in self._character_image_paths:
            if img_path.exists():
                parts.append(types.Part.from_bytes(
                    data=img_path.read_bytes(),
                    mime_type="image/png",
                ))
        parts.append(types.Part.from_text(
            text=_SCENE_PROMPT.format(expression=expression, premise=script.premise),
        ))

        config = types.GenerateContentConfig(
            temperature=0.7,
            top_p=0.9,
            response_modalities=["IMAGE"],
        )
        contents = [types.Content(role="user", parts=parts)]

        response = self._gemini_generate_with_retry(contents, config)

        if not response.candidates:
            raise GeminiImageError("No candidates returned from Gemini")

        candidate = response.candidates[0]
        if not candidate.content or not candidate.content.parts:
            raise GeminiImageError("No content parts in response")

        image_part = None
        for part in candidate.content.parts:
            if part.inline_data and part.inline_data.mime_type.startswith("image/"):
                image_part = part
                break

        if not image_part or not image_part.inline_data:
            raise GeminiImageError("No image data in response")

        inline_data = image_part.inline_data.data
        if isinstance(inline_data, str):
            image_bytes = base64.b64decode(inline_data)
        else:
            image_bytes = inline_data

        return Image.open(io.BytesIO(image_bytes))

    def _gemini_generate_with_retry(
        self, contents: list, config: object, retries: int = 3, backoff: float = 10.0,
    ) -> object:
        for attempt in range(1, retries + 1):
            try:
                return self._gemini_client.client.models.generate_content(
                    model=self._gemini_client.model,
                    contents=contents,
                    config=config,
                )
            except Exception as exc:
                is_rate_limit = "429" in str(exc) or "RESOURCE_EXHAUSTED" in str(exc)
                if is_rate_limit and attempt < retries:
                    wait = backoff * attempt
                    logger.warning(
                        "Gemini rate limited; retrying in %ss (attempt %s/%s)",
                        wait, attempt, retries,
                    )
                    time.sleep(wait)
                else:
                    raise

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

        # Position text in the lower portion (~75-85% down)
        total_text_height = sum(line_heights) + line_spacing * max(len(lines) - 1, 0)
        y_center = int(_THUMBNAIL_HEIGHT * 0.80)
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
