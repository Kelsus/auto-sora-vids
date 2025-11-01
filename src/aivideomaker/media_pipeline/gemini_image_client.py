from __future__ import annotations

import base64
import json
import logging
from pathlib import Path
from typing import Any, Optional

from google import genai
from google.genai import types

logger = logging.getLogger(__name__)


class GeminiImageError(RuntimeError):
    """Raised when Gemini image generation fails."""


class GeminiImageClient:
    """Client for Gemini Flash image generation via Vertex AI."""

    def __init__(
        self,
        output_dir: Path | None = None,
        api_key: str | None = None,
        model: str = "gemini-2.0-flash-exp",
        use_vertex: bool = True,
        project: str | None = None,
        location: str | None = None,
        credentials_path: Path | None = None,
    ) -> None:
        self._output_dir = Path(output_dir) if output_dir is not None else None
        self.api_key = api_key
        self.model = model
        self.use_vertex = use_vertex
        self.project = project
        self.location = location or "us-central1"
        self.credentials_path = Path(credentials_path) if credentials_path else None
        self._credentials = None
        self.client = self._build_client()
        
        if self.use_vertex:
            logger.info("Initialized Gemini Image client for Vertex AI project %s", self.project)
        else:
            logger.info("Initialized Gemini Image client with API key")

    @property
    def output_dir(self) -> Path:
        if self._output_dir is None:
            raise RuntimeError("Gemini output directory is not configured")
        return self._output_dir

    @output_dir.setter
    def output_dir(self, value: Path) -> None:
        self._output_dir = Path(value)

    def generate_scene_with_chart(
        self,
        scene_description: str,
        chart_path: Path,
        output_dir: Path,
        clip_id: str,
    ) -> Path:
        """
        Generate a composite image showing the chart in a contextual scene.
        
        Args:
            scene_description: Static scene prompt (no motion directions)
            chart_path: Path to the chart PNG to composite into the scene
            output_dir: Directory to save the generated image
            clip_id: Identifier for this clip
            
        Returns:
            Path to the generated composite image
            
        Raises:
            GeminiImageError: If generation fails
        """
        if not self.client:
            raise RuntimeError("Gemini client is not configured")
        
        output_dir.mkdir(parents=True, exist_ok=True)
        target = output_dir / f"{clip_id}_composite.png"
        
        logger.info("🎨 Generating composite scene for %s", clip_id)
        
        # Read chart image
        if not chart_path.exists():
            raise GeminiImageError(f"Chart image not found: {chart_path}")
        
        chart_data = chart_path.read_bytes()
        
        # Build prompt with chart reference
        full_prompt = self._build_composite_prompt(scene_description, chart_path)
        
        try:
            # Create image generation request
            config = types.GenerateContentConfig(
                temperature=0.7,
                top_p=0.9,
                response_modalities=["IMAGE"],
            )
            
            # Include chart as reference image
            contents = [
                types.Content(
                    role="user",
                    parts=[
                        types.Part.from_text(text=full_prompt),
                        types.Part.from_bytes(data=chart_data, mime_type="image/png"),
                    ],
                ),
            ]
            
            response = self.client.models.generate_content(
                model=self.model,
                contents=contents,
                config=config,
            )
            
            # Extract generated image
            if not response.candidates:
                raise GeminiImageError("No candidates returned from Gemini")
            
            candidate = response.candidates[0]
            if not candidate.content or not candidate.content.parts:
                raise GeminiImageError("No content parts in response")
            
            # Find image part
            image_part = None
            for part in candidate.content.parts:
                if part.inline_data and part.inline_data.mime_type.startswith("image/"):
                    image_part = part
                    break
            
            if not image_part or not image_part.inline_data:
                raise GeminiImageError("No image data in response")
            
            # Save image
            inline_data = image_part.inline_data.data
            if isinstance(inline_data, str):
                image_bytes = base64.b64decode(inline_data)
            else:
                image_bytes = inline_data
            target.write_bytes(image_bytes)
            
            logger.info("✅ Generated composite image at %s", target)
            return target
            
        except Exception as exc:
            logger.error("Failed to generate composite image for %s: %s", clip_id, exc)
            raise GeminiImageError(f"Image generation failed: {exc}") from exc

    def _build_composite_prompt(self, scene_description: str, chart_path: Path) -> str:
        """Build the full prompt for composite image generation."""
        return (
            f"{scene_description}\n\n"
            f"CRITICAL REQUIREMENTS:\n"
            f"- Incorporate the provided chart/data visualization into the scene naturally\n"
            f"- The chart should be clearly visible and prominent in the composition\n"
            f"- Place the chart on a wall, screen, tablet, or other appropriate surface\n"
            f"- Maintain professional documentary aesthetic\n"
            f"- Use cinematic lighting that makes both scene and chart readable\n"
            f"- Aspect ratio: 9:16 (vertical/portrait for short-form video)\n"
            f"- No camera blur, no motion blur - this is a still image\n"
            f"- No text overlays or captions beyond what's in the chart itself"
        )

    # Client setup helpers -------------------------------------------------

    def _build_client(self) -> Optional[genai.Client]:
        """Build the Gemini API client."""
        if self.use_vertex:
            return self._build_vertex_client()
        if self.api_key:
            logger.info("Using Gemini API key authentication")
            return genai.Client(api_key=self.api_key)
        logger.warning("No Gemini credentials provided; client will not function")
        return None

    def _build_vertex_client(self) -> genai.Client:
        """Build Vertex AI client with service account credentials."""
        if not self.project:
            raise RuntimeError("Vertex AI project ID is required")
        
        # Load credentials if path provided
        if self.credentials_path and self.credentials_path.exists():
            logger.info("Loading Vertex AI credentials from %s", self.credentials_path)
            creds_data = json.loads(self.credentials_path.read_text(encoding="utf-8"))
            self._credentials = creds_data
        
        import vertexai
        from google.auth import default
        from google.oauth2 import service_account
        
        if self._credentials:
            credentials = service_account.Credentials.from_service_account_info(
                self._credentials,
                scopes=["https://www.googleapis.com/auth/cloud-platform"],
            )
        else:
            credentials, _ = default()
        
        vertexai.init(
            project=self.project,
            location=self.location,
            credentials=credentials,
        )
        
        client = genai.Client(
            vertexai=True,
            project=self.project,
            location=self.location,
        )
        
        logger.info("Initialized Vertex AI Gemini client for project %s, location %s", self.project, self.location)
        return client


__all__ = ["GeminiImageClient", "GeminiImageError"]
