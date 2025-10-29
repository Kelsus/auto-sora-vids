from __future__ import annotations

import logging
from math import fabs
from pathlib import Path
from typing import Iterable

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
        mark = (chart_spec.mark or "arc").lower()
        try:
            if mark in {"arc", "pie", "donut"}:
                self._render_pie(chart_spec, target)
            elif mark == "bar":
                self._render_bar(chart_spec, target)
            elif mark in {"line", "area"}:
                self._render_line(chart_spec, target, fill_area=(mark == "area"))
            else:
                logger.warning("Chart mark '%s' not supported; falling back to pie visualization.", chart_spec.mark)
                self._render_pie(chart_spec, target)
        except Exception as exc:
            logger.error("Failed to render chart %s: %s", name, exc)
            raise
        return target

    # ------------------------------------------------------------------
    # Pie / Donut
    # ------------------------------------------------------------------

    def _render_pie(self, chart_spec: ChartSpec, target: Path) -> None:
        values_payload = chart_spec.data.get("values", [])
        if not values_payload:
            raise ValueError("Pie chart requires 'values'")

        labels = [item.get("label", "") for item in values_payload]
        values = [float(item.get("value", 0)) for item in values_payload]
        note = chart_spec.data.get("note")

        width = int(chart_spec.width or 1080)
        height = int(chart_spec.height or 1920)
        img = Image.new("RGB", (width, height), color=(16, 19, 26))
        draw = ImageDraw.Draw(img)

        total = sum(values) or 1.0
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
                f"{label} ({values[idx]:g})",
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

    # ------------------------------------------------------------------
    # Bar chart
    # ------------------------------------------------------------------

    def _render_bar(self, chart_spec: ChartSpec, target: Path) -> None:
        values_payload = chart_spec.data.get("values", [])
        if not values_payload:
            raise ValueError("Bar chart requires 'values'")

        width = int(chart_spec.width or 1080)
        height = int(chart_spec.height or 1920)
        img = Image.new("RGB", (width, height), color=(16, 20, 28))
        draw = ImageDraw.Draw(img)

        labels = [item.get("label", "") for item in values_payload]
        numbers = [float(item.get("value", 0)) for item in values_payload]
        max_value = max(max(numbers), 1.0)

        margin_x = int(width * 0.12)
        margin_top = int(height * 0.22)
        margin_bottom = int(height * 0.18)
        chart_height = height - margin_top - margin_bottom
        chart_width = width - 2 * margin_x
        slot_width = chart_width / max(len(values_payload), 1)
        bar_width = slot_width * 0.6

        palette = [
            (93, 173, 226),
            (155, 89, 182),
            (243, 156, 18),
            (46, 204, 113),
            (192, 57, 43),
        ]
        axis_color = (120, 130, 145)
        draw.line(
            [(margin_x, margin_top), (margin_x, margin_top + chart_height)],
            fill=axis_color,
            width=3,
        )
        draw.line(
            [(margin_x, margin_top + chart_height), (margin_x + chart_width, margin_top + chart_height)],
            fill=axis_color,
            width=3,
        )

        for idx, value in enumerate(numbers):
            bar_height = 0 if max_value == 0 else (value / max_value) * chart_height
            x_center = margin_x + (idx + 0.5) * slot_width
            x0 = x_center - bar_width / 2
            x1 = x_center + bar_width / 2
            y1 = margin_top + chart_height
            y0 = y1 - bar_height
            draw.rounded_rectangle(
                [x0, y0, x1, y1],
                radius=12,
                fill=palette[idx % len(palette)],
            )
            label_font = self._load_font(size=40)
            value_font = self._load_font(size=44)
            draw.text(
                (x_center, y1 + 24),
                labels[idx],
                font=label_font,
                fill=(220, 225, 230),
                anchor="ma",
            )
            draw.text(
                (x_center, y0 - 24),
                f"{value:g}",
                font=value_font,
                fill=(230, 235, 242),
                anchor="mb",
            )

        note = chart_spec.data.get("note")
        if note:
            draw.text(
                (width // 2, int(height * 0.12)),
                note,
                font=self._load_font(size=48),
                fill=(230, 235, 242),
                anchor="mm",
            )

        img.save(target, format="PNG")

    # ------------------------------------------------------------------
    # Line / Area chart
    # ------------------------------------------------------------------

    def _render_line(self, chart_spec: ChartSpec, target: Path, *, fill_area: bool) -> None:
        points_payload = chart_spec.data.get("points") or chart_spec.data.get("values")
        if not points_payload:
            raise ValueError("Line chart requires 'points' or 'values'")

        width = int(chart_spec.width or 1080)
        height = int(chart_spec.height or 1920)
        img = Image.new("RGB", (width, height), color=(12, 16, 26))
        draw = ImageDraw.Draw(img)

        labels = [item.get("label", str(idx)) for idx, item in enumerate(points_payload)]
        numbers = [float(item.get("value", 0)) for item in points_payload]
        max_value = max(max(numbers), 1.0)
        min_value = min(min(numbers), 0.0)
        if fabs(max_value - min_value) < 1e-6:
            max_value = min_value + 1.0

        margin_x = int(width * 0.12)
        margin_top = int(height * 0.22)
        margin_bottom = int(height * 0.2)
        chart_height = height - margin_top - margin_bottom
        chart_width = width - 2 * margin_x

        axis_color = (110, 120, 135)
        draw.line(
            [(margin_x, margin_top), (margin_x, margin_top + chart_height)],
            fill=axis_color,
            width=3,
        )
        draw.line(
            [(margin_x, margin_top + chart_height), (margin_x + chart_width, margin_top + chart_height)],
            fill=axis_color,
            width=3,
        )

        coords: list[tuple[float, float]] = []
        for idx, value in enumerate(numbers):
            x = margin_x + (idx / max(len(numbers) - 1, 1)) * chart_width
            normalized = (value - min_value) / (max_value - min_value)
            y = margin_top + chart_height * (1 - normalized)
            coords.append((x, y))

        line_color = (82, 156, 255)
        if fill_area and len(coords) >= 2:
            polygon = [
                (coords[0][0], margin_top + chart_height),
                *coords,
                (coords[-1][0], margin_top + chart_height),
            ]
            draw.polygon(polygon, fill=(82, 156, 255, 70))
        if len(coords) >= 2:
            draw.line(coords, fill=line_color, width=6, joint="curve")

        for (x, y), label, value in zip(coords, labels, numbers):
            draw.ellipse([x - 10, y - 10, x + 10, y + 10], fill=line_color)
            draw.text(
                (x, y - 26),
                f"{value:g}",
                font=self._load_font(size=40),
                fill=(230, 235, 242),
                anchor="mb",
            )
            draw.text(
                (x, margin_top + chart_height + 28),
                label,
                font=self._load_font(size=38),
                fill=(215, 220, 230),
                anchor="ma",
            )

        note = chart_spec.data.get("note")
        if note:
            draw.text(
                (width // 2, int(height * 0.12)),
                note,
                font=self._load_font(size=48),
                fill=(230, 235, 242),
                anchor="mm",
            )

        img.save(target, format="PNG")

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    def _load_font(self, size: int = 32) -> ImageFont.FreeTypeFont:
        try:
            return ImageFont.truetype("DejaVuSans.ttf", size=size)
        except Exception:  # pragma: no cover
            return ImageFont.load_default()
