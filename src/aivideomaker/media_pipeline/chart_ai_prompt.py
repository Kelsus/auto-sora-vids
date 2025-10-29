from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import List, Optional

from aivideomaker.script_engine.model import Beat, BeatVisualSpec


@dataclass
class ChartDataPoint:
    label: str
    value: float
    secondary_value: Optional[float] = None
    series: Optional[str] = None


@dataclass
class ChartCodeSpec:
    variant: str
    title: Optional[str]
    subtitle: Optional[str]
    x_label: Optional[str]
    y_label: Optional[str]
    note: Optional[str]
    reason: Optional[str]
    width: int = 1080
    height: int = 1920
    data_points: List[ChartDataPoint] = field(default_factory=list)

    def to_payload(self) -> dict:
        return {
            "variant": self.variant,
            "title": self.title,
            "subtitle": self.subtitle,
            "x_label": self.x_label,
            "y_label": self.y_label,
            "note": self.note,
            "reason": self.reason,
            "width": self.width,
            "height": self.height,
            "data_points": [
                {
                    "label": point.label,
                    "value": point.value,
                    "secondary_value": point.secondary_value,
                    "series": point.series,
                }
                for point in self.data_points
            ],
        }


def build_chart_codegen_spec(beat: Beat, *, width: int = 1080, height: int = 1920) -> ChartCodeSpec:
    visual = beat.visual
    if not visual or (visual.type or "").lower() != "chart":
        raise ValueError("Beat does not contain a chart visual specification")

    data_points = _extract_data_points(visual)
    if not data_points:
        raise ValueError("Chart visual is missing data points")

    variant = _resolve_chart_variant(visual, data_points)

    return ChartCodeSpec(
        variant=variant,
        title=visual.chart_title or beat.purpose,
        subtitle=visual.chart_subtitle,
        x_label=visual.chart_x_label,
        y_label=visual.chart_y_label,
        note=visual.chart_note,
        reason=visual.chart_reason,
        width=width,
        height=height,
        data_points=data_points,
    )


def build_chart_codegen_prompt(spec: ChartCodeSpec) -> str:
    payload_json = json.dumps(spec.to_payload(), indent=2)
    instructions = (
        "You are an expert data visualization coder. Generate a professional {variant} chart as a PNG image using Python.\n\n"
        "CRITICAL REQUIREMENTS:\n"
        "1. Import required libraries at the top:\n"
        "   import matplotlib.pyplot as plt\n"
        "   import matplotlib\n"
        "   (Add numpy or other libraries only if needed)\n\n"
        "2. Create figure with EXACT dimensions:\n"
        "   matplotlib.use('Agg')  # Use non-interactive backend\n"
        "   fig, ax = plt.subplots(figsize=({width_in:.1f}, {height_in:.1f}))\n"
        "   fig.set_dpi(100)\n\n"
        "3. Use ONLY the data points provided in the specification below:\n"
        "   - Do NOT fabricate, interpolate, or add extra values\n"
        "   - Use exact labels and values as specified\n\n"
        "4. Style requirements (MUST FOLLOW):\n"
        "   - Background: Dark (#10141C or similar dark color)\n"
        "   - Data colors: Vivid accent colors (bright blues, purples, oranges, greens)\n"
        "   - Fonts: Sans-serif (Arial, Helvetica, DejaVu Sans)\n"
        "   - Aesthetic: Professional, clean, documentary-style\n\n"
        "5. Include all provided elements:\n"
        "   - Title (large, prominent at top)\n"
        "   - Subtitle (if provided)\n"
        "   - Axis labels (x_label, y_label if provided)\n"
        "   - Note/annotation (if provided, as footnote)\n\n"
        "6. Chart-specific requirements:\n"
        "   - Bar: Vertical bars with clear spacing, values displayed on top\n"
        "   - Line: Smooth lines with visible markers at data points\n"
        "   - Pie/Donut: Clear labels and legend, use percentages\n\n"
        "7. SAVE FILE (CRITICAL - must be exact):\n"
        "   plt.tight_layout()\n"
        "   plt.savefig('chart.png', dpi=100, facecolor=fig.get_facecolor(), edgecolor='none')\n"
        "   plt.close()\n\n"
        "8. Do NOT call plt.show() - only save to 'chart.png'\n\n"
        "Generate complete, executable Python code. Include all imports. Make the chart visually striking and professional while using only the provided data.\n\n"
        "Chart specification JSON follows:\n{payload}"
    ).format(
        variant=spec.variant,
        width=spec.width,
        height=spec.height,
        width_in=spec.width / 100,
        height_in=spec.height / 100,
        payload=payload_json
    )
    return instructions


def _extract_data_points(visual: BeatVisualSpec) -> List[ChartDataPoint]:
    raw_points = visual.chart_series or []
    data_points: List[ChartDataPoint] = []
    for item in raw_points:
        if item is None:
            continue
        label = str(item.get("label") or item.get("category") or item.get("time") or "").strip()
        if not label:
            continue
        value_raw = item.get("value")
        try:
            value = float(value_raw)
        except (TypeError, ValueError):
            continue
        secondary = item.get("secondary_value")
        try:
            secondary_value = float(secondary) if secondary is not None else None
        except (TypeError, ValueError):
            secondary_value = None
        series = item.get("series") or item.get("group")
        if series is not None:
            series = str(series)
        data_points.append(
            ChartDataPoint(
                label=label,
                value=value,
                secondary_value=secondary_value,
                series=series,
            )
        )
    return data_points


def _resolve_chart_variant(visual: BeatVisualSpec, data_points: List[ChartDataPoint]) -> str:
    desired = _normalize_variant(visual.chart_variant)

    spec_variant = _normalize_variant(_variant_from_spec_id(visual.spec_id))
    if spec_variant and spec_variant != "bar":
        return spec_variant

    reason_text = " ".join(filter(None, [visual.chart_reason, visual.chart_note])).lower()
    values = [point.value for point in data_points if point.value is not None]
    total = sum(values) if values else 0.0

    if values and _looks_temporal(data_points, reason_text):
        return "line"

    if values and _looks_composition(values, total, reason_text):
        return "donut"

    if any(point.series for point in data_points):
        return "combo"

    if len(data_points) >= 6 and _looks_sequential_labels(data_points):
        return "line"

    if desired and desired != "bar":
        return desired

    if spec_variant:
        return spec_variant

    return desired or "bar"


def _variant_from_spec_id(spec_id: Optional[str]) -> Optional[str]:
    if not spec_id:
        return None
    name = spec_id.lower()
    if "donut" in name or "pie" in name or "arc" in name:
        return "donut"
    if "line" in name:
        return "line"
    if "area" in name:
        return "area"
    if "combo" in name or "stack" in name:
        return "combo"
    if "bar" in name:
        return "bar"
    return None


def _normalize_variant(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    normalized = value.strip().lower()
    alias_map = {
        "pie": "donut",
        "arc": "donut",
        "column": "bar",
        "stacked": "stacked_bar",
        "stacked_bar": "stacked_bar",
        "radial": "donut",
    }
    return alias_map.get(normalized, normalized)


def _looks_temporal(points: List[ChartDataPoint], reason: str) -> bool:
    if any(keyword in reason for keyword in ("trend", "over time", "timeline", "year")):
        return True
    temporal_hits = 0
    months = {
        "jan",
        "feb",
        "mar",
        "apr",
        "may",
        "jun",
        "jul",
        "aug",
        "sep",
        "sept",
        "oct",
        "nov",
        "dec",
    }
    for point in points:
        label = point.label.lower()
        if label.isdigit() and len(label) == 4:
            temporal_hits += 1
        elif any(label.startswith(month) for month in months):
            temporal_hits += 1
    return temporal_hits >= max(2, len(points) // 2)


def _looks_composition(values: List[float], total: float, reason: str) -> bool:
    if not values:
        return False
    if any(keyword in reason for keyword in ("share", "percentage", "split", "breakdown", "composition")):
        return True
    if total <= 0:
        return False
    if all(0 <= value <= 100 for value in values) and 90 <= total <= 110:
        return True
    return False


def _looks_sequential_labels(points: List[ChartDataPoint]) -> bool:
    labels = [point.label for point in points]
    numeric = []
    for label in labels:
        try:
            numeric.append(float(label.replace(",", "")))
        except ValueError:
            return False
    return numeric == sorted(numeric)
