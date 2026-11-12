"""Bundled drivetrain databases and stored-preset recovery.

The JSON files in ``data/`` are package data. They are updated with the
library when new cassettes and chainring sets appear. After editing
``front_rings.json`` or ``cassettes.json``, run
``python scripts/update_gearing_data.py`` to rebuild ``preset_index.json``.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from importlib.resources import files

from . import pdu
from .client import BikeError, DrivetrainMode, GearSettings, SB20Client
from .state import BikeState


@dataclass(frozen=True, slots=True)
class GearSet:
    name: str
    gears: tuple[int, ...]
    manufacturer: str = ""


def _load(filename: str) -> tuple[GearSet, ...]:
    path = files("openstage").joinpath("data").joinpath(filename)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot load {filename}: {exc}") from exc
    if not isinstance(raw, list):
        raise ValueError(f"{filename}: expected an array of gear sets")
    result: list[GearSet] = []
    names: set[str] = set()
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError(f"{filename}[{index}]: expected an object")
        name = item.get("name")
        gears = item.get("gears")
        manufacturer = item.get("manufacturer", "")
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"{filename}[{index}]: invalid name")
        if not isinstance(manufacturer, str):
            raise ValueError(f"{filename}[{index}]: invalid manufacturer")
        if not isinstance(gears, list) or not gears:
            raise ValueError(f"{filename}[{index}]: invalid gears")
        values = tuple(gears)
        if any(
            isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 255
            for value in values
        ):
            raise ValueError(f"{filename}[{index}]: gears must be integers 1..255")
        if len(set(values)) != len(values):
            raise ValueError(f"{filename}[{index}]: duplicate gears")
        if name in names:
            raise ValueError(f"{filename}[{index}]: duplicate name {name!r}")
        names.add(name)
        result.append(GearSet(name=name, gears=values, manufacturer=manufacturer))
    return tuple(result)


def load_front_rings() -> tuple[GearSet, ...]:
    return _load("front_rings.json")


def load_cassettes() -> tuple[GearSet, ...]:
    return _load("cassettes.json")


def build_preset_index() -> dict[str, list[str]]:
    """Build the fingerprint index from the curated drivetrain tables."""

    return pdu.build_preset_index(
        (front.gears for front in load_front_rings()),
        (cassette.gears for cassette in load_cassettes()),
    )


def _strip_comments(source: str) -> str:
    """Drop ``//`` line comments, which the generated index uses as annotations."""

    result: list[str] = []
    index = 0
    in_string = False
    while index < len(source):
        char = source[index]
        if in_string:
            result.append(char)
            if char == "\\" and index + 1 < len(source):
                result.append(source[index + 1])
                index += 2
                continue
            in_string = char != '"'
            index += 1
        elif char == '"':
            in_string = True
            result.append(char)
            index += 1
        elif source.startswith("//", index):
            newline = source.find("\n", index)
            if newline < 0:
                break
            index = newline
        else:
            result.append(char)
            index += 1
    return "".join(result)


@lru_cache(maxsize=1)
def load_preset_index() -> dict[str, tuple[bytes, ...]]:
    """Load the precomputed fingerprint index shipped with the package."""

    path = files("openstage").joinpath("data").joinpath("preset_index.json")
    try:
        raw = json.loads(_strip_comments(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot load preset_index.json: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError("preset_index.json: expected an object")
    result: dict[str, tuple[bytes, ...]] = {}
    for key, values in raw.items():
        if not isinstance(key, str) or not isinstance(values, list) or not values:
            raise ValueError(f"preset_index.json: invalid entry {key!r}")
        payloads: list[bytes] = []
        for value in values:
            if not isinstance(value, str):
                raise ValueError(f"preset_index.json: invalid payload for {key!r}")
            try:
                payloads.append(bytes.fromhex(value))
            except ValueError as exc:
                raise ValueError(
                    f"preset_index.json: invalid hex for {key!r}"
                ) from exc
        result[key] = tuple(payloads)
    return result


def settings_for_stored_header(
    client: SB20Client,
    header: pdu.GearModelHeader,
    fragments: Sequence[bytes] = (),
    gear: pdu.GearNotification | None = None,
) -> GearSettings | None:
    """Recover the bundled preset that produced a stored flash header.

    The SB20 cannot replay a stored map, and its map readback is incomplete.
    The stored header still carries the map CRC, and the first read fragment
    carries the first bytes of the stored map. Match those against the
    precomputed fingerprint index. Returns ``None`` when the header is
    ambiguous or not in the bundled data.
    """

    if header.model is pdu.ModelType.COEFFICIENTS:
        if not header.dream_drive:
            return None
        try:
            settings = (
                client.gears()
                .set_mode(DrivetrainMode.DREAM_DRIVE)
                .set_gears_number(header.rear_count)
                .set_front_shift(header.dream_drive_step)
            )
            plan = settings.plan()
        except ValueError:
            return None
        return settings if plan.matches(header) else None

    if header.model is not pdu.ModelType.FRONT_REAR_U8 or header.dream_drive:
        return None

    key = f"{header.front_count}x{header.rear_count}:{header.crc8:02X}"
    payloads = list(load_preset_index().get(key, ()))
    if fragments:
        prefix = fragments[0]
        payloads = [
            payload for payload in payloads if payload[: len(prefix)] == prefix
        ]
    if (
        len(payloads) > 1
        and gear is not None
        and gear.model is pdu.ModelType.FRONT_REAR_U8
        and header.front_count == 1
    ):
        payloads = [payload for payload in payloads if payload[0] == gear.front_teeth]
    if len(payloads) != 1:
        return None
    payload = payloads[0]
    if len(payload) != header.map_size:
        return None
    settings = (
        client.gears()
        .set_mode(DrivetrainMode.SIMULATED_CASSETTE)
        .set_front_gears(tuple(payload[: header.front_count]))
        .set_cassette_gears(tuple(payload[header.front_count :]))
    )
    return settings if settings.plan().matches(header) else None


async def resync_drivetrain(
    client: SB20Client,
    state: BikeState,
    results: Mapping[str, pdu.Pdu | Exception],
    fragments: Sequence[bytes] = (),
) -> str | None:
    """Re-apply the drivetrain preset that flash holds.

    A header read reports flash but neither reloads nor proves the active RAM
    map, so a session can start with RAM and flash disagreeing and the live
    gear notifications then describe a drivetrain the app was not told about.
    This identifies the stored preset in the bundled data and applies it,
    which also refills the transfer buffer for a later store.

    ``results`` is a completed :meth:`SB20Client.query_all`; ``fragments`` are
    the map fragments observed while it ran. Returns a message for the caller's
    log, or ``None`` when the query carried no header.
    """

    header = results.get("gear_model")
    if not isinstance(header, pdu.GearModelHeader):
        return None
    state.ingest(header, pdu.Stream.API)
    gear = results.get("current_gear")
    current = gear if isinstance(gear, pdu.GearNotification) else None
    settings = settings_for_stored_header(client, header, fragments, current)
    if settings is None:
        return (
            "drivetrain: stored preset not in the bundled database or "
            "ambiguous; RAM may differ from flash"
        )
    plan = settings.plan()
    try:
        await settings.commit()
    except BikeError as exc:
        return f"drivetrain resync failed: {exc}"
    state.set_known_drivetrain(plan.as_header(), plan.map_bytes)
    try:
        state.ingest(await client.get_current_gear(), pdu.Stream.API)
    except BikeError:
        pass
    return (
        "drivetrain: re-applied stored preset "
        f"({plan.front_count}×{plan.rear_count}, CRC 0x{plan.crc8:02X})"
    )
