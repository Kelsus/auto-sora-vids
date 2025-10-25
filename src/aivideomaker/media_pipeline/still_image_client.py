from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

try:
    from google import genai
    from vertexai.preview.vision_models import ImageGenerationModel
except ImportError:  # pragma: no cover - optional dependency
    genai = None  # type: ignore[assignment]
    ImageGenerationModel = None  # type: ignore[assignment]

from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)


class StillImageClient:
    """Generate high-resolution stills for beats that should avoid full video generation."""

    def __init__(
        self,
        asset_dir: Path,
        api_key: Optional[str] = None,
        model: str = "imagen-3.0-nano-banana",
        use_vertex: bool = False,
        project: Optional[str] = None,
        location: Optional[str] = None,
    ) -> None:
        self.asset_dir = Path(asset_dir)
        self.asset_dir.mkdir(parents=True, exist_ok=True)
        self.api_key = api_key
        self.model = model
        self.use_vertex = use_vertex
        self.project = project
        self.location = location or "us-central1"
        self.client = self._build_client()

    def _build_client(self):  # pragma: no cover - runtime dependency
        if self.use_vertex:
            if ImageGenerationModel is None:
                logger.warning(
                    "Vertex AI client not available; falling back to stub still generator."
                )
                return None
            try:
                return ImageGenerationModel.from_pretrained(self.model)
            except Exception as exc:  # pragma: no cover
                logger.error("Failed to initialize Vertex image model: %s", exc)
                return None

        if not self.api_key or genai is None:
            if self.api_key and genai is None:
                logger.warning(
                    "Google Generative AI SDK not available; falling back to stub still generator."
                )
            return None
        try:
            client = genai.Client(api_key=self.api_key)
            return client.models.generate_images
        except Exception as exc:  # pragma: no cover
            logger.error("Failed to initialize still image client: %s", exc)
            return None

    def generate(self, prompt: str, negative: Optional[str], output_name: str) -> Path:
        target = self.asset_dir / f"{output_name}.png"
        if self.client is None:
            return self._generate_stub_image(prompt, target)

        try:  # pragma: no cover - requires external service
            if self.use_vertex and self.client is not None:
                result = self.client.generate_images(
                    prompt=prompt,
                    negative_prompt=negative or "",
                    number_of_images=1,
                )
                image_data = result[0]._image_bytes  # type: ignore[attr-defined]
            elif callable(self.client):
                result = self.client(
                    model=self.model,
                    prompt=prompt,
                    negative_prompt=negative or "",
                )
                image_data = result.generated_images[0].image
            else:
                response = self.client.generate_images(
                    prompt=prompt,
                    negative_prompt=negative or "",
                )
                image_data = response.images[0].data

            with target.open("wb") as handle:
                handle.write(image_data)
            logger.info("🖼️  Generated still via nano banana → %s", target)
            return target
        except Exception as exc:
            logger.error("Failed to generate still via nano banana: %s", exc)
            return self._generate_stub_image(prompt, target)

    def _generate_stub_image(self, prompt: str, target: Path) -> Path:
        width, height = 1080, 1920
        img = Image.new("RGB", (width, height), color=(20, 24, 32))
        draw = ImageDraw.Draw(img)
        padding = 60
        text_area = prompt[:500] + ("…" if len(prompt) > 500 else "")
        lines = []
        words = text_area.split()
        line = []
        max_width = width - 2 * padding
        font = self._load_font()
        for word in words:
            candidate = " ".join(line + [word]) if line else word
            if draw.textlength(candidate, font=font) <= max_width:
                line.append(word)
            else:
                lines.append(" ".join(line))
                line = [word]
        if line:
            lines.append(" ".join(line))
        y = padding
        for line in lines[:30]:
            draw.text((padding, y), line, font=font, fill=(220, 225, 230))
            y += font.size + 6
        img.save(target, format="PNG")
        logger.info("🖼️  Created stub still image → %s", target)
        return target

    def _load_font(self) -> ImageFont.FreeTypeFont:
        try:
            return ImageFont.truetype("DejaVuSans.ttf", size=42)
        except Exception:  # pragma: no cover
            return ImageFont.load_default()
