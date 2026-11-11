"""Tests for the bundled drivetrain databases and stored-preset recovery."""

from __future__ import annotations

from openstage import pdu
from openstage.client import SB20Client


def test_gearing_databases_load_and_cover_expected_sets() -> None:
    from openstage.gearing_data import load_cassettes, load_front_rings

    fronts = load_front_rings()
    cassettes = load_cassettes()
    assert len(fronts) == 30
    assert len(cassettes) == 180
    assert all(1 <= len(item.gears) <= 2 for item in fronts)
    assert all(7 <= len(item.gears) <= 13 for item in cassettes)
    assert any(item.manufacturer == "SRAM" for item in cassettes)
    assert any(item.name == "1x 40T" and item.gears == (40,) for item in fronts)
    assert any(item.name == "2x 34/50" and item.gears == (34, 50) for item in fronts)
    assert any(
        item.name.startswith("12/10-33")
        and item.gears == (10, 11, 12, 13, 14, 15, 17, 19, 21, 24, 28, 33)
        for item in cassettes
    )


def test_cassettes_are_sorted_for_display() -> None:
    from openstage.gearing_data import GearSet, load_cassettes

    def display_key(item: GearSet) -> tuple[int, str, int, int]:
        return (
            -len(item.gears),
            item.manufacturer.casefold(),
            -max(item.gears),
            min(item.gears),
        )

    keys = [display_key(item) for item in load_cassettes()]
    assert keys == sorted(keys)


def test_preset_index_matches_bundled_data() -> None:
    from openstage.gearing_data import build_preset_index, load_preset_index

    built = build_preset_index()
    expected = {
        key: tuple(bytes.fromhex(value) for value in values)
        for key, values in built.items()
    }
    assert load_preset_index() == expected


def test_preset_index_comments_do_not_break_parsing() -> None:
    from openstage.gearing_data import _strip_comments

    assert _strip_comments('{"a": "x//y"  // note\n}') == '{"a": "x//y"  \n}'


def test_stored_preset_matching() -> None:
    from openstage.gearing_data import settings_for_stored_header

    client = SB20Client()
    header = pdu.decode_pdu(bytes.fromhex("0c020800010c0f00040d00"))
    assert isinstance(header, pdu.GearModelHeader)
    gear13 = pdu.decode_pdu(bytes.fromhex("0c0100010d22000b000004"))
    assert isinstance(gear13, pdu.GearNotification)
    gear3 = pdu.decode_pdu(bytes.fromhex("0c0100010322001c000004"))
    assert isinstance(gear3, pdu.GearNotification)

    # CRC 0x0F matches 1x32/11-29 and 1x34/10-36; current front ring 34 picks
    # the latter even without the partial map.
    settings = settings_for_stored_header(client, header, [], gear13)
    assert settings is not None
    plan = settings.plan()
    assert plan.front_gears == (34,)
    assert plan.cassette_gears == (36, 32, 28, 24, 21, 19, 17, 15, 13, 12, 11, 10)
    assert plan.crc8 == 0x0F
    assert plan.matches_gear(gear3) is True
    assert plan.matches_gear(gear13) is False

    # The first-read fragment carries the stored map prefix.
    prefix = bytes.fromhex("2224201c18151311")
    settings = settings_for_stored_header(client, header, [prefix], None)
    assert settings is not None
    assert settings.plan().front_gears == (34,)

    # Without either disambiguator the header is ambiguous.
    assert settings_for_stored_header(client, header, [], None) is None


