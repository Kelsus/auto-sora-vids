import json
import logging

import pytest

from aivideomaker.script_engine.utils import extract_json_block, load_json_with_repair


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("{\"visual_type\": \"chart\"}", {"visual_type": "chart"}),
        (
            "```json\n{\n  \"visual_type\": \"still_motion\"\n}\n```",
            {"visual_type": "still_motion"},
        ),
        (
            "Sure thing!\n{\"visual_type\": \"chart\"}\nThanks!",
            {"visual_type": "chart"},
        ),
        (
            "Confidence interval {95%}\n{\"visual_type\": \"chart\"}\n(note: approximate)",
            {"visual_type": "chart"},
        ),
    ],
)
def test_extract_json_block_handles_noise(raw: str, expected: dict[str, str]) -> None:
    extracted = extract_json_block(raw)
    assert json.loads(extracted) == expected


def test_load_json_with_repair_parses_embedded_json() -> None:
    raw = "Here you go:\n```json\n{\"visual_type\": \"chart\"}\n```\nConfidence: high"
    data = load_json_with_repair(raw, logger=logging.getLogger(__name__))
    assert data == {"visual_type": "chart"}
