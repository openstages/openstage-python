"""Elite Square profile tests: encoders first, then a live client session."""

from __future__ import annotations

import asyncio
from uuid import UUID

import pytest

from openstage.wftnp import (
    BUTTONS,
    SERVICE,
    STATUS,
    DerailleurGear,
    EliteSquare,
    SequentialGear,
    WftnpServer,
    elite_button,
)


def _mask(elite: EliteSquare) -> int:
    return int.from_bytes(elite.encode_status()[3:7], "big")


def _nibble(elite: EliteSquare, button: int) -> int:
    page = elite.encode_buttons()
    return (page[1 + (button - 1) // 2] >> (4 * ((button - 1) % 2))) & 0x0F


def test_status_sequential_layout_matches_the_spec_example() -> None:
    elite = EliteSquare(
        active_profile=1,
        battery=0x57,
        gear=SequentialGear(total=14, ratio=0x09C4, current=7),
    )
    assert elite.encode_status() == bytes.fromhex("0101570000000000 0e09c407".replace(" ", ""))


def test_status_derailleur_layout_matches_the_spec_example() -> None:
    elite = EliteSquare(
        active_profile=1,
        battery=0x57,
        gear=DerailleurGear(
            front_count=2,
            rear_count=12,
            front_teeth=0x34,
            rear_teeth=0x1C,
            current_front=1,
            current_rear=5,
        ),
    )
    assert elite.encode_status() == bytes.fromhex("0001570000000000 020c341c0105".replace(" ", ""))


def test_button_page_is_a_state_snapshot() -> None:
    elite = EliteSquare()
    assert elite.encode_buttons() == bytes(11)
    elite.press(1)
    assert elite.encode_buttons() == bytes.fromhex("0001000000000000000000")
    elite.release(1)
    assert elite.encode_buttons() == bytes.fromhex("0002000000000000000000")
    elite.press(1)
    assert elite.encode_buttons() == bytes.fromhex("0003000000000000000000")

    elite.reset()
    elite.press(3)
    assert elite.encode_buttons() == bytes.fromhex("0000010000000000000000")

    elite.reset()
    elite.press(1)
    elite.press(2)
    assert elite.encode_buttons() == bytes.fromhex("0011000000000000000000")


def test_counters_wrap_at_four_bits_and_track_the_pressed_bit() -> None:
    elite = EliteSquare()
    for _ in range(7):
        assert elite.press(1)
        assert elite.release(1)
    assert _nibble(elite, 1) == 0b1110
    elite.press(1)
    assert _nibble(elite, 1) == 0b1111
    elite.release(1)
    assert _nibble(elite, 1) == 0b0000


def test_pressed_mask_is_big_endian_and_matches_the_page() -> None:
    elite = EliteSquare()
    elite.press(17)
    assert _mask(elite) == 1 << 16
    assert elite.encode_status()[3:7] == bytes.fromhex("00010000")


def test_buttons_map_by_position() -> None:
    assert [elite_button(n) for n in range(1, 15)] == [
        9, 10, 1, 19, 20, 2, 8, 18, 3, 4, 11, 12, 13, 14,
    ]
    with pytest.raises(ValueError):
        elite_button(15)


def test_set_buttons_maps_by_position() -> None:
    elite = EliteSquare()
    assert elite.set_buttons(0b1)
    assert _mask(elite) == 1 << 8  # physical 1 is Elite 9
    assert _nibble(elite, 9) == 1
    assert not elite.set_buttons(0b1)

    elite.reset()
    assert elite.set_buttons(0b1000)  # physical 4 is Elite 19
    assert _mask(elite) == 1 << 18


def test_reset_clears_everything() -> None:
    elite = EliteSquare()
    elite.set_buttons(0xFFFF)
    elite.press(20)
    elite.reset()
    assert _mask(elite) == 0
    assert elite.encode_buttons() == bytes(11)


def test_gear_and_battery_validation() -> None:
    with pytest.raises(ValueError):
        SequentialGear(total=3, ratio=0, current=4)
    with pytest.raises(ValueError):
        SequentialGear(total=24, ratio=0x10000, current=1)
    with pytest.raises(ValueError):
        DerailleurGear(
            front_count=1, rear_count=12, front_teeth=34, rear_teeth=28, current_rear=13
        )
    with pytest.raises(ValueError):
        EliteSquare(battery=101)


async def test_client_sees_the_elite_service() -> None:
    wftnp = pytest.importorskip("wftnp")
    elite = EliteSquare(battery=87)
    resets: list[int] = []

    async def reset() -> None:
        resets.append(1)
        elite.reset()

    server = WftnpServer(
        name="Square Test",
        port=0,
        advertise=False,
        services=[elite.service],
        txt={
            "serial-number": "SQ-0001",
            "mac-address": "02:00:00:00:00:01",
            "ble-service-uuids": SERVICE,
        },
        on_disconnect=reset,
    )
    await server.start()
    client = wftnp.WftnpClient(
        wftnp.Endpoint("127.0.0.1", server.port),
        auto_reconnect=False,
        heartbeat_interval=None,
    )
    try:
        await client.start()
        await client.wait_ready(timeout=5)
        assert [str(service.uuid) for service in await client.discover_services()] == [SERVICE]

        characteristics = await client.discover_characteristics(UUID(SERVICE))
        assert {str(ch.uuid) for ch in characteristics} == {STATUS, BUTTONS}
        assert all(int(ch.properties) & 0x05 == 0x05 for ch in characteristics)

        status = await client.read_characteristic(UUID(STATUS))
        assert len(status) == 14
        assert status[:3] == bytes([0, 1, 87])

        subscription = await client.subscribe(UUID(BUTTONS))
        try:
            initial = await asyncio.wait_for(anext(subscription), 2)
            assert initial.value == bytes(11)
            assert elite.set_buttons(0b1)
            await server.notify(BUTTONS, elite.encode_buttons())
            note = await asyncio.wait_for(anext(subscription), 2)
            assert note.value == bytes.fromhex("0000000000010000000000")
        finally:
            await subscription.close()
    finally:
        await client.stop()
        await server.stop()

    assert resets
    assert _mask(elite) == 0
