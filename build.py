"""Generate the drivetrain preset fingerprint index before packaging.

Loaded by poetry-core as the project build script.  It must not import the
runtime package (the isolated build environment has no BLE/Textual
dependencies), so the pure codec is loaded directly from its file.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
PDU_PATH = ROOT / "openstage" / "pdu.py"
DATA = ROOT / "openstage" / "data"


def _load_pdu():
    spec = importlib.util.spec_from_file_location("openstage_pdu_build", PDU_PATH)
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
    pdu, fronts: list[dict], cassettes: list[dict]
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


def main() -> None:
    pdu = _load_pdu()
    fronts = json.loads((DATA / "front_rings.json").read_text(encoding="utf-8"))
    cassettes = json.loads((DATA / "cassettes.json").read_text(encoding="utf-8"))
    index = pdu.build_preset_index(
        (tuple(item["gears"]) for item in fronts),
        (tuple(item["gears"]) for item in cassettes),
    )
    (DATA / "preset_index.json").write_text(
        _render(index, _payload_labels(pdu, fronts, cassettes)),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
