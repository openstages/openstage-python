"""Capture loading and replay tests."""

from __future__ import annotations

from contextlib import aclosing
from pathlib import Path

import pytest

from openstage import pdu
from openstage.capture import Capture, CapturedExchange, CapturedFrame, load_capture
from openstage.client import Notification
from openstage.pdu import Stream
from openstage.replay import ReplayClient
from openstage.state import BikeState

FIXTURES = Path(__file__).parent / "fixtures" / "legacy"


def test_legacy_capture_loader_understands_request_fields() -> None:
    capture = load_capture(FIXTURES / "queries.json")
    assert capture.identity.name == "Stages Bike 0071"
    assert capture.identity.serial == "H0603210071"
    assert capture.mtu == 23
    requests = [exchange.hex for exchange in capture.exchanges]
    assert "0e00" in requests
    assert "0c0207" in requests
    assert capture.exchanges[0].responses[0].hex() == "0e000000000201"


def test_capture_roundtrip(tmp_path: Path) -> None:
    capture = Capture(
        frames=(
            CapturedFrame(t=0.1, stream=Stream.API, raw=bytes.fromhex("0e000000000201")),
            CapturedFrame(t=0.2, stream=Stream.BUTTONS, raw=bytes.fromhex("01000400")),
        ),
        exchanges=(CapturedExchange(request=b"\x0e\x00",
                                    responses=(bytes.fromhex("0e000000000201"),)),),
    )
    path = tmp_path / "session.json"
    capture.write(path)
    loaded = load_capture(path)
    assert loaded.mtu is None
    assert len(loaded.frames) == 2
    assert loaded.frames[0].stream is Stream.API
    assert loaded.frames[1].raw == b"\x01\x00\x04\x00"
    assert loaded.exchanges[0].request == b"\x0e\x00"


async def test_replay_queries_feeds_typed_state() -> None:
    client = ReplayClient(FIXTURES / "queries.json", speed=1000)
    state = BikeState()
    await client.connect()
    state.identity = client.identity
    state.mtu = client.mtu

    async for note in client.notifications():
        state.apply(note)

    await client.close()
    assert state.resistance.value is not None
    assert state.resistance.value.level == 0
    assert state.weight.value is not None
    assert state.weight.value.user_kg == 84.0
    assert state.buttons.value is not None
    assert state.buttons.value.by_slot[1] == 3
    assert state.drivetrain_view().map_bytes == 8


async def test_replay_ride_feeds_telemetry() -> None:
    client = ReplayClient(FIXTURES / "ride.json", speed=1000)
    state = BikeState()
    await client.connect()
    async for note in client.notifications():
        state.apply(note)
    await client.close()

    assert state.power_w.value is not None
    assert state.cadence_rpm.value is not None
    assert state.counts[Stream.API] > 200
    assert state.counts[Stream.FTMS_DATA] > 100
    assert state.gear.value is not None
    assert state.gear.value.front_teeth == 34


async def test_replay_can_loop_and_be_closed() -> None:
    client = ReplayClient(FIXTURES / "buttons.json", speed=1000, loop=True)
    await client.connect()
    async with aclosing(client.notifications()) as iterator:
        first = await anext(iterator)
        assert first.stream is Stream.BUTTONS
        await client.close()
        with pytest.raises(StopAsyncIteration):
            await anext(iterator)


def test_decode_every_legacy_frame() -> None:
    for name in ("queries.json", "ride.json", "buttons.json"):
        capture = load_capture(FIXTURES / name)
        for frame in capture.frames:
            record = pdu.decode_notification(frame.stream, frame.raw)
            assert isinstance(record, pdu.Pdu)
            assert record.raw == frame.raw
        for exchange in capture.exchanges:
            for raw in exchange.responses:
                assert isinstance(pdu.decode_notification(Stream.API, raw), pdu.Pdu)
