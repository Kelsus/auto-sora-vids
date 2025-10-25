from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

from aivideomaker.orchestrator_chart_models import ChartSpec

logger = logging.getLogger(__name__)


class ChartRenderer:
    """Render simple charts described in the bundle's chart_specs."""

    def __init__(self, output_dir: Path) -> None:
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def render(self, chart_spec: ChartSpec, name: str) -> Path:
        target = self.output_dir / f"{name}.png"
        try:
            if chart_spec.mark == "arc":
                self._render_pie(chart_spec, target)
            else:
                logger.warning("Chart mark '%s' not supported; falling back to arc visualization.", chart_spec.mark)
                self._render_pie(chart_spec, target)
        except Exception as exc:
            logger.error("Failed to render chart %s: %s", name, exc)
            raise
        return target

    def _render_pie(self, chart_spec: ChartSpec, target: Path) -> None:
        data_values = chart_spec.data.get("values", [])
        labels = [item.get("label", "") for item in data_values]
        values = [item.get("value", 0) for item in data_values]
        note = chart_spec.data.get("note")

        width = int(chart_spec.width or 1080)
        height = int(chart_spec.height or 1920)
        img = Image.new("RGB", (width, height), color=(16, 19, 26))
        draw = ImageDraw.Draw(img)

        total = sum(values) or 1
        start_angle = -90.0
        radius = int(min(width, height) * 0.6)
        center_x = width // 2
        center_y = int(height * 0.45)
        bbox = [
            center_x - radius // 2,
            center_y - radius // 2,
            center_x + radius // 2,
            center_y + radius // 2,
        ]

        palette = [
            (92, 225, 230),
            (139, 92, 246),
            (244, 114, 182),
            (34, 197, 94),
            (249, 115, 22),
        ]
        for idx, value in enumerate(values):
            if value <= 0:
                continue
            angle = 360.0 * float(value) / total
            draw.pieslice(bbox, start_angle, start_angle + angle, fill=palette[idx % len(palette)])
            start_angle += angle

        inner_radius = int(radius * 0.55)
        inner_bbox = [
            center_x - inner_radius // 2,
            center_y - inner_radius // 2,
            center_x + inner_radius // 2,
            center_y + inner_radius // 2,
        ]
        draw.ellipse(inner_bbox, fill=(16, 19, 26))

        legend_top = int(height * 0.75)
        legend_left = width // 2 - 200
        legend_spacing = 50
        font = self._load_font()
        for idx, label in enumerate(labels):
            color = palette[idx % len(palette)]
            draw.rectangle(
                [legend_left, legend_top + idx * legend_spacing, legend_left + 36, legend_top + idx * legend_spacing + 36],
                fill=color,
            )
            draw.text(
                (legend_left + 48, legend_top + idx * legend_spacing + 5),
                f"{label} ({values[idx]})",
                fill=(230, 235, 242),
                font=font,
            )

        if note:
            draw.text(
                (width // 2, int(height * 0.1)),
                note,
                fill=(230, 235, 242),
                font=self._load_font(size=48),
                anchor="mm",
            )

        img.save(target, format="PNG")

    def _load_font(self, size: int = 32) -> ImageFont.FreeTypeFont:
        try:
            return ImageFont.truetype("DejaVuSans.ttf", size=size)
        except Exception:  # pragma: no cover
            return ImageFont.load_default()
