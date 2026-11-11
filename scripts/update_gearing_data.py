"""Refresh the cassette table from the configured gear data source.

Usage::

    python scripts/update_gearing_data.py [SOURCE]

``SOURCE`` is a URL or local path to the source JSON file and defaults to the
URL in this script. The bundled ``cassettes.json`` is rewritten in display
order (cog count, manufacturer, largest cog, smallest cog) and
``preset_index.json`` is regenerated from it.

The index is generated here rather than at build time, so the distribution
stays a pure Python wheel. The codec is loaded straight from its file because
the package pulls in BLE and UI dependencies this script does not need.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path
from types import ModuleType
from typing import Any
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "openstage" / "data"
PDU_PATH = ROOT / "openstage" / "pdu.py"
SOURCE_URL = (
    "https://raw.githubusercontent.com/dirkelko/Gear-Calculator-UI5/"
    "refs/heads/master/webapp/model/Gears.json"
)

# Canonical brand spellings; upstream descriptions carry the brand name.
_BRANDS: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(pattern, re.IGNORECASE), name)
    for pattern, name in (
        (r"\bcampagnolo\b", "Campagnolo"),
        (r"\bsram\b", "SRAM"),
        (r"\bshimano\b", "Shimano"),
        (r"\bmiche\b", "Miche"),
        (r"\bclassified\b", "Classified"),
        (r"e\*thirteen", "e*thirteen"),
        (r"e\*13\b", "e*thirteen"),
        (r"\bgarbaruk\b", "Garbaruk"),
        (r"\brotor\b", "Rotor"),
        (r"\bsunrace\b", "Sunrace"),
        (r"\boneup\b", "OneUp"),
        (r"\bhope\b", "Hope"),
        (r"\b3t\b", "3T"),
        (r"\bleonardi\b", "Leonardi"),
        (r"\bbox\b", "Box"),
        (r"\bsuntour\b", "Suntour"),
    )
)
_VARIANT = re.compile(r"([A-Za-z]+)\s*$")


def _manufacturer(description: str) -> str:
    for pattern, name in _BRANDS:
        if pattern.search(description):
            return name
    return ""


def _order(record: dict[str, Any]) -> tuple[int, str, int, int, str]:
    """Newest cog count first, then brand, largest cog, and smallest cog."""

    gears: list[int] = record["gears"]
    return (
        -len(gears),
        record["manufacturer"].casefold(),
        -max(gears),
        min(gears),
        record["name"].casefold(),
    )


def _records(source: dict[str, Any]) -> list[dict[str, Any]]:
    """Convert the upstream cog sets into sorted cassette records."""

    result: list[dict[str, Any]] = []
    names: set[str] = set()
    for item in source["CogSets"]:
        gears = list(item["set"])
        if not gears:
            continue
        name = str(item["description"]).strip()
        if name in names:
            match = _VARIANT.search(str(item["name"]))
            name = f"{name} ({match.group(1)})" if match else name
        if name in names:
            raise ValueError(f"duplicate upstream cassette name: {name!r}")
        names.add(name)
        result.append(
            {
                "name": name,
                "manufacturer": _manufacturer(name),
                "gears": gears,
            }
        )
    result.sort(key=_order)
    return result


def _read(source: str) -> dict[str, Any]:
    if source.startswith(("http://", "https://")):
        with urlopen(source, timeout=60) as response:
            return json.load(response)
    return json.loads(Path(source).read_text(encoding="utf-8"))


def _load_pdu() -> ModuleType:
    spec = importlib.util.spec_from_file_location("openstage_pdu_update", PDU_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {PDU_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(spec.name, None)
    return module


def _payload_labels(
    pdu: ModuleType, fronts: list[dict[str, Any]], cassettes: list[dict[str, Any]]
) -> dict[str, tuple[str, list[str]]]:
    """Map each map payload (hex) to its front ring and cassette names.

    A payload can come from several cassette names when two brands ship the
    same tooth sequence, so every name is kept.
    """

    labels: dict[str, tuple[str, list[str]]] = {}
    for front in fronts:
        front_bytes = bytes(pdu.sorted_front_gears(front["gears"]))
        for cassette in cassettes:
            payload = front_bytes + bytes(
                pdu.sorted_cassette_gears(cassette["gears"])
            )
            front_name, cassette_names = labels.setdefault(
                payload.hex(), (front["name"], [])
            )
            if cassette["name"] not in cassette_names:
                cassette_names.append(cassette["name"])
    return labels


def _render(
    index: dict[str, list[str]],
    labels: dict[str, tuple[str, list[str]]],
) -> str:
    """Render the index as JSON with a ``//`` comment naming each payload."""

    items = sorted(index.items())
    lines = ["{"]
    for key_position, (key, values) in enumerate(items):
        lines.append(f"    {json.dumps(key)}: [")
        for value_position, value in enumerate(values):
            comma = "," if value_position + 1 < len(values) else ""
            front_name, cassette_names = labels[value]
            comment = f"{front_name} : {'; '.join(cassette_names)}"
            lines.append(f"        {json.dumps(value)}{comma}  // {comment}")
        lines.append("    ]" + ("," if key_position + 1 < len(items) else ""))
    lines.append("}")
    return "\n".join(lines) + "\n"


def _preset_index_text() -> str:
    """Rebuild ``preset_index.json`` from the bundled drivetrain tables."""

    pdu = _load_pdu()
    fronts = json.loads((DATA / "front_rings.json").read_text(encoding="utf-8"))
    cassettes = json.loads((DATA / "cassettes.json").read_text(encoding="utf-8"))
    index = pdu.build_preset_index(
        (tuple(item["gears"]) for item in fronts),
        (tuple(item["gears"]) for item in cassettes),
    )
    return _render(index, _payload_labels(pdu, fronts, cassettes))


def main(argv: list[str]) -> None:
    source = argv[1] if len(argv) > 1 else SOURCE_URL
    records = _records(_read(source))
    (DATA / "cassettes.json").write_text(
        json.dumps(records, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (DATA / "preset_index.json").write_text(
        _preset_index_text(), encoding="utf-8"
    )
    print(f"wrote {len(records)} cassettes and rebuilt preset_index.json")


if __name__ == "__main__":
    main(sys.argv)
