"""Refresh the cassette table from the configured gear data source.

Usage::

    python scripts/update_gearing_data.py [SOURCE]

``SOURCE`` is a URL or local path to the source JSON file and defaults to the
URL in this script. The bundled ``cassettes.json`` is rewritten in display
order (cog count, manufacturer, largest cog, smallest cog) and
``preset_index.json`` is regenerated from it.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "openstage" / "data"
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


def main(argv: list[str]) -> None:
    source = argv[1] if len(argv) > 1 else SOURCE_URL
    records = _records(_read(source))
    (DATA / "cassettes.json").write_text(
        json.dumps(records, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    sys.path.insert(0, str(ROOT))
    from build import main as build_index

    build_index()
    print(f"wrote {len(records)} cassettes and rebuilt preset_index.json")


if __name__ == "__main__":
    main(sys.argv)
