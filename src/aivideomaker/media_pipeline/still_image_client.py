from __future__ import annotations

import logging
import os
from io import BytesIO
from pathlib import Path
from typing import Optional

try:
    from google import genai
    from google.genai.types import FinishReason, GenerateContentConfig, ImageConfig, Part
except ImportError:  # pragma: no cover - optional dependency
    genai = None  # type: ignore[assignment]
    GenerateContentConfig = None  # type: ignore[assignment]
    ImageConfig = None  # type: ignore[assignment]
    Part = None  # type: ignore[assignment]
    FinishReason = None  # type: ignore[assignment]

from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)


class StillImageClient:
    """Generate high-resolution stills for beats that should avoid full video generation."""

    def __init__(
        self,
        asset_dir: Path,
        api_key: Optional[str] = None,
        model: str = "gemini-2.5-flash-image",
        use_vertex: bool = False,
        project: Optional[str] = None,
        location: Optional[str] = None,
        credentials_path: Optional[Path] = None,
    ) -> None:
        self.asset_dir = Path(asset_dir)
        self.asset_dir.mkdir(parents=True, exist_ok=True)
        self.api_key = api_key
        self.model = model
        self.use_vertex = use_vertex
        env_credential = os.getenv("GEMINI_KEY_FILE")
        resolved_path: Optional[Path] = None
        if credentials_path:
            resolved_path = Path(credentials_path)
        elif env_credential:
            resolved_path = Path(env_credential)
        self.project = project
        self.location = location or "us-central1"
        self.credentials_path = resolved_path
        self.client = self._build_client()

    def _build_client(self):  # pragma: no cover - runtime dependency
        if self.use_vertex:
            if genai is None or GenerateContentConfig is None or ImageConfig is None or Part is None or FinishReason is None:
                logger.warning(
                    "Vertex AI generative client not available; falling back to stub still generator."
                )
                return None
            if not self.project:
                logger.warning(
                    "Vertex project is not configured; falling back to stub still generator."
                )
                return None
            if self.credentials_path:
                credentials_str = str(self.credentials_path.expanduser().resolve())
                current = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
                if current and Path(current) != Path(credentials_str):
                    logger.debug(
                        "Overriding GOOGLE_APPLICATION_CREDENTIALS from %s to %s for still generation",
                        current,
                        credentials_str,
                    )
                os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = credentials_str
            elif not os.getenv("GOOGLE_APPLICATION_CREDENTIALS"):
                logger.warning(
                    "GOOGLE_APPLICATION_CREDENTIALS is not set; Vertex still generation will likely fail."
                )
            try:
                return genai.Client(vertexai=True, project=self.project, location=self.location)
            except Exception as exc:  # pragma: no cover
                logger.error("Failed to initialize Vertex image client: %s", exc)
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

    def generate(
        self,
        prompt: str,
        negative: Optional[str],
        output_name: str,
        *,
        image_prompts: Optional[list[Path]] = None,
        aspect_ratio: Optional[str] = None,
    ) -> Path:
        target = self.asset_dir / f"{output_name}.png"
        if self.client is None:
            return self._generate_stub_image(prompt, target)

        try:  # pragma: no cover - requires external service
            if self.use_vertex and self.client is not None:
                contents = []
                if image_prompts:
                    for image_path in image_prompts:
                        try:
                            contents.append(
                                Part.from_bytes(
                                    data=Path(image_path).read_bytes(),
                                    mime_type="image/png",
                                )
                            )
                        except Exception as exc:  # pragma: no cover - logging aid
                            logger.warning(
                                "Failed to attach image prompt %s: %s", image_path, exc
                            )
                contents.append(prompt)

                # Build config following Gemini SDK patterns
                config_kwargs = {
                    "response_modalities": ["IMAGE"],
                    "candidate_count": 1,
                }
                if aspect_ratio:
                    config_kwargs["image_config"] = ImageConfig(aspect_ratio=aspect_ratio)
                
                config = GenerateContentConfig(**config_kwargs)
                
                logger.debug("Calling Gemini %s with %d content parts", self.model, len(contents))
                response = self.client.models.generate_content(
                    model=self.model,
                    contents=contents,
                    config=config,
                )
                
                # Check for errors per the example notebook pattern
                if response.candidates and len(response.candidates) > 0:
                    finish_reason = response.candidates[0].finish_reason
                    if finish_reason != FinishReason.STOP:
                        raise RuntimeError(
                            f"Gemini image generation failed with finish_reason: {finish_reason}. "
                            f"This may indicate content policy violations or other issues."
                        )
                
                # Extract the image from the response
                # Note: inline_data.data is already binary data, NOT base64
                image_data = None
                for candidate in response.candidates or []:
                    for part in candidate.content.parts:
                        if hasattr(part, "inline_data") and part.inline_data:
                            if hasattr(part.inline_data, "data") and part.inline_data.data:
                                image_data = part.inline_data.data
                                break
                    if image_data:
                        break
                
                if image_data is None:
                    raise RuntimeError("Vertex model returned no inline image data")
                
                # Save the image directly (data is already binary, not base64)
                with Image.open(BytesIO(image_data)) as generated_img:
                    generated_img.convert("RGB").save(target, format="PNG")
                logger.info("🖼️  Generated still via Gemini image model → %s", target)
                return target
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
