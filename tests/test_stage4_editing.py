"""Stage4 editing parser and settings tests."""
from __future__ import annotations

import json

from inference.parsers import parse_editing_suggestions_response
from services.processor.stages.stage4_editing_runner import stage4_editing_settings


def test_parse_editing_suggestions_response() -> None:
    raw = json.dumps(
        {
            "editing_suggestions": [
                {"zh": "曝光 +0.3", "en": "Exposure +0.3 EV"},
                {"zh": "裁切 4:5", "en": "Crop 4:5"},
            ]
        }
    )
    out = parse_editing_suggestions_response(raw)
    assert len(out) == 2
    assert out[0]["en"] == "Exposure +0.3 EV"


def test_stage4_editing_settings_defaults() -> None:
    cfg = stage4_editing_settings({"stage4_editing": {"enabled": True}})
    assert cfg["enabled"] is True
    assert cfg["num_predict"] >= 128
