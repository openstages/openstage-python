"""Tests for the gear data update script."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType


SCRIPT = (
    Path(__file__).resolve().parents[1] / "scripts" / "update_gearing_data.py"
)


def _script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("update_gearing_data", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_preset_index_matches_script_output() -> None:
    data = SCRIPT.parents[1] / "openstage" / "data"
    assert _script()._preset_index_text() == (
        data / "preset_index.json"
    ).read_text(encoding="utf-8")


def test_records_are_annotated_sorted_and_deduplicated() -> None:
    records = _script()._records(
        {
            "CogSets": [
                {"name": "10/11-21", "description": "10/11-21", "set": [11, 12, 13, 14, 15, 16, 17, 18, 19, 21]},
                {
                    "name": "12/11-44SR",
                    "description": "12/11-44 SRAM XPLR",
                    "set": [11, 12, 13, 14, 15, 16, 17, 19, 21, 24, 28, 44],
                },
                {
                    "name": "12/10-44SR",
                    "description": "12/10-44 SRAM XPLR",
                    "set": [10, 11, 12, 13, 14, 15, 16, 17, 19, 21, 24, 44],
                },
                {
                    "name": "12/09-45ET",
                    "description": "12/9-45 e*13 XCX",
                    "set": [9, 10, 11, 12, 13, 14, 15, 16, 18, 21, 24, 45],
                },
                {
                    "name": "11/11-45OA",
                    "description": "11/11-45 OneUp",
                    "set": [11, 12, 13, 14, 15, 17, 19, 21, 24, 28, 45],
                },
                {
                    "name": "11/11-45OB",
                    "description": "11/11-45 OneUp",
                    "set": [11, 12, 13, 14, 15, 17, 19, 21, 24, 28, 44],
                },
                {"name": "custom", "description": "custom", "set": []},
            ]
        }
    )
    assert [(item["name"], item["manufacturer"]) for item in records] == [
        ("12/9-45 e*13 XCX", "e*thirteen"),
        ("12/10-44 SRAM XPLR", "SRAM"),
        ("12/11-44 SRAM XPLR", "SRAM"),
        ("11/11-45 OneUp", "OneUp"),
        ("11/11-45 OneUp (OB)", "OneUp"),
        ("10/11-21", ""),
    ]
