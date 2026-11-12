"""Bridge tests with a fake bike: notifications out, writes back in."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

import pytest

from openstage import pdu
from openstage.client import Notification, SB20Client, WriteResult
from openstage.wftnp import BUTTONS, STATUS, WftnpBridge

API_REQUEST = "0c46beb1-9c22-48ff-ae0e-c6eae1a2f4e5"
API_RESPONSE = "0c46beb0-9c22-48ff-ae0e-c6eae1a2f4e5"
FTMS_DATA = "00002ad2-0000-1000-8000-00805f9b34fb"
FTMS_CONTROL = "00002ad9-0000-1000-8000-00805f9b34fb"
TXT = {
    "serial-number": "SQ-0001",
    "mac-address": "02:00:00:00:00:01",
    "ble-service-uuids": "347b0001-7635-408b-8918-8ff3949ce592",
}


class FakeBike:
    """Only the calls the bridge makes."""

    def __init__(self) -> None:
        self.queue: asyncio.Queue[Notification | None] = asyncio.Queue()
        self.weights: list[tuple[float, float]] = []
        self.controls: list[bytes] = []
        self.answer = pdu.Weight(
            raw=bytes.fromhex("0301e803e803"),
            op=pdu.Op.WEIGHT,
            user_kg=10.0,
            equipment_kg=10.0,
        )

    async def notifications(self) -> AsyncIterator[Notification]:
        while True:
            item = await self.queue.get()
            if item is None:
                return
            yield item

    async def set_weight(self, user_kg: float, equipment_kg: float) -> WriteResult:
        self.weights.append((user_kg, equipment_kg))
        return WriteResult(set_response=self.answer)

    async def ftms_control(self, value: bytes) -> None:
        self.controls.append(bytes(value))

    def push(self, stream: pdu.Stream, record: pdu.Pdu) -> None:
        self.queue.put_nowait(
            Notification(stream=stream, raw=record.raw, record=record, t=time.monotonic())
        )


@pytest.fixture
async def setup() -> AsyncIterator[tuple[FakeBike, WftnpBridge]]:
    bike = FakeBike()
    bridge = WftnpBridge(bike, name="Square Bridge", txt=TXT, advertise=False, port=0)  # type: ignore[arg-type]
    await bridge.start()
    try:
        yield bike, bridge
    finally:
        await bridge.stop()


async def _initial(subscription: Any) -> bytes:
    note = await asyncio.wait_for(anext(subscription), 2)
    return note.value


async def _connect(bridge: WftnpBridge) -> Any:
    wftnp = pytest.importorskip("wftnp")
    client = wftnp.WftnpClient(
        wftnp.Endpoint("127.0.0.1", bridge.server.port),
        auto_reconnect=False,
        heartbeat_interval=None,
    )
    await client.start()
    await client.wait_ready(timeout=5)
    return client


def _header(model: pdu.ModelType, front: int, rear: int) -> pdu.GearModelHeader:
    return pdu.GearModelHeader(
        raw=b"",
        op=pdu.Op.GEARING,
        command=pdu.GearingTransferCommand.GET_CURRENT_MODEL_SUCCESS,
        model=model,
        front_count=front,
        rear_count=rear,
        crc8=0,
        dream_drive=model is pdu.ModelType.COEFFICIENTS,
        dream_drive_step=0,
        map_size=rear * 2 if model is pdu.ModelType.COEFFICIENTS else front + rear,
    )


async def test_buttons_reach_the_app_with_elite_numbers(
    setup: tuple[FakeBike, WftnpBridge],
) -> None:
    bike, bridge = setup
    client = await _connect(bridge)
    subscription = await client.subscribe(UUID(BUTTONS))
    try:
        await _initial(subscription)
        bike.push(
            pdu.Stream.BUTTONS,
            pdu.ButtonFrame(
                raw=bytes.fromhex("01000100"),
                op=pdu.Op.BUTTONS,
                selector=1,
                header=0,
                values=(1,),
            ),
        )
        note = await asyncio.wait_for(anext(subscription), 2)
        # Physical 1 maps to Elite 9 by position: page byte 5.
        assert note.value == bytes.fromhex("0000000000010000000000")
    finally:
        await subscription.close()
        await client.stop()


async def test_derailleur_gear_reaches_the_status_value(
    setup: tuple[FakeBike, WftnpBridge],
) -> None:
    bike, bridge = setup
    client = await _connect(bridge)
    subscription = await client.subscribe(UUID(STATUS))
    try:
        await _initial(subscription)
        bike.push(pdu.Stream.API, _header(pdu.ModelType.FRONT_REAR_U8, 2, 12))
        bike.push(
            pdu.Stream.API,
            pdu.GearNotification(
                raw=b"",
                op=pdu.Op.GEARING,
                model=pdu.ModelType.FRONT_REAR_U8,
                front_index=1,
                rear_index=5,
                front_teeth=34,
                rear_teeth=28,
                dream_drive=False,
                dream_drive_step=0,
            ),
        )
        note = await asyncio.wait_for(anext(subscription), 2)
        assert note.value == bytes.fromhex("0001640000000000020c221c0105")
    finally:
        await subscription.close()
        await client.stop()


async def test_dream_drive_gear_carries_the_raw_coefficient(
    setup: tuple[FakeBike, WftnpBridge],
) -> None:
    bike, bridge = setup
    client = await _connect(bridge)
    subscription = await client.subscribe(UUID(STATUS))
    try:
        await _initial(subscription)
        bike.push(pdu.Stream.API, _header(pdu.ModelType.COEFFICIENTS, 1, 14))
        bike.push(
            pdu.Stream.API,
            pdu.GearNotification(
                raw=b"",
                op=pdu.Op.GEARING,
                model=pdu.ModelType.COEFFICIENTS,
                front_index=0,
                rear_index=6,
                front_teeth=0x09C4,
                rear_teeth=0,
                dream_drive=True,
                dream_drive_step=0,
            ),
        )
        note = await asyncio.wait_for(anext(subscription), 2)
        assert note.value == bytes.fromhex("01016400000000000e09c406")
    finally:
        await subscription.close()
        await client.stop()


async def test_ftms_indoor_bike_data_is_relayed(
    setup: tuple[FakeBike, WftnpBridge],
) -> None:
    bike, bridge = setup
    client = await _connect(bridge)
    subscription = await client.subscribe(UUID(FTMS_DATA))
    try:
        await _initial(subscription)
        record = pdu.decode_notification(pdu.Stream.FTMS_DATA, bytes.fromhex("0000c800"))
        bike.push(pdu.Stream.FTMS_DATA, record)
        note = await asyncio.wait_for(anext(subscription), 2)
        assert note.value == bytes.fromhex("0000c800")
    finally:
        await subscription.close()
        await client.stop()


async def test_weight_write_is_forwarded_and_answered(
    setup: tuple[FakeBike, WftnpBridge],
) -> None:
    bike, bridge = setup
    client = await _connect(bridge)
    subscription = await client.subscribe(UUID(API_RESPONSE))
    try:
        await client.write_characteristic(UUID(API_REQUEST), bytes.fromhex("0301e803e803"))
        note = await asyncio.wait_for(anext(subscription), 2)
        assert bike.weights == [(10.0, 10.0)]
        assert note.value == bike.answer.raw
    finally:
        await subscription.close()
        await client.stop()


async def test_other_stages_writes_are_refused(
    setup: tuple[FakeBike, WftnpBridge],
) -> None:
    bike, bridge = setup
    wftnp = pytest.importorskip("wftnp")
    client = await _connect(bridge)
    try:
        with pytest.raises(wftnp.OperationRejected):
            await client.write_characteristic(UUID(API_REQUEST), bytes.fromhex("0600"))
        assert bike.weights == []
    finally:
        await client.stop()


async def test_ftms_control_point_writes_reach_the_bike(
    setup: tuple[FakeBike, WftnpBridge],
) -> None:
    bike, bridge = setup
    client = await _connect(bridge)
    try:
        value = bytes.fromhex("11000000000000")
        await client.write_characteristic(UUID(FTMS_CONTROL), value)
        assert bike.controls == [value]
    finally:
        await client.stop()


async def test_cassette_report_wins_over_a_dream_drive_header(
    setup: tuple[FakeBike, WftnpBridge],
) -> None:
    """A bike whose flash holds Dream Drive can still report a cassette."""

    bike, bridge = setup
    client = await _connect(bridge)
    subscription = await client.subscribe(UUID(STATUS))
    try:
        await _initial(subscription)
        bike.push(pdu.Stream.API, _header(pdu.ModelType.COEFFICIENTS, 1, 24))
        bike.push(
            pdu.Stream.API,
            pdu.GearNotification(
                raw=b"",
                op=pdu.Op.GEARING,
                model=pdu.ModelType.FRONT_REAR_U8,
                front_index=1,
                rear_index=4,
                front_teeth=34,
                rear_teeth=28,
                dream_drive=False,
                dream_drive_step=4,
            ),
        )
        note = await asyncio.wait_for(anext(subscription), 2)
        assert note.value == bytes.fromhex("00016400000000000104221c0104")
    finally:
        await subscription.close()
        await client.stop()


async def test_brake_levers_press_the_elite_paddles(
    setup: tuple[FakeBike, WftnpBridge],
) -> None:
    bike, bridge = setup
    client = await _connect(bridge)
    subscription = await client.subscribe(UUID(BUTTONS))
    try:
        await _initial(subscription)
        bike.push(
            pdu.Stream.BRAKE,
            pdu.BrakeFrame(raw=b"", op=0xBE61, left=1000, right=0, extra=()),
        )
        note = await asyncio.wait_for(anext(subscription), 2)
        assert note.value == bytes.fromhex("0000000001000000000000")  # Elite 7
        status = await client.read_characteristic(UUID(STATUS))
        assert int.from_bytes(status[3:7], "big") == 1 << 6

        bike.push(
            pdu.Stream.BRAKE,
            pdu.BrakeFrame(raw=b"", op=0xBE61, left=0, right=1000, extra=()),
        )
        note = await asyncio.wait_for(anext(subscription), 2)
        assert note.value == bytes.fromhex("0000000002000000000100")  # 7 released, 17 pressed
    finally:
        await subscription.close()
        await client.stop()


class _GearingBike(SB20Client):
    """An SB20Client stand-in that records the gearing subscription."""

    def __init__(self) -> None:
        self.queue: asyncio.Queue[Notification | None] = asyncio.Queue()
        self.gearing_subscriptions = 0
        self.queries = 0

    async def notifications(self) -> AsyncIterator[Notification]:
        while True:
            item = await self.queue.get()
            if item is None:
                return
            yield item

    async def query_all(self) -> dict[str, object]:
        self.queries += 1
        return {}

    def add_listener(self, listener: object) -> None:
        return None

    def remove_listener(self, listener: object) -> None:
        return None

    async def subscribe_gearing(self) -> None:
        self.gearing_subscriptions += 1

    async def set_weight(self, user_kg: float, equipment_kg: float) -> WriteResult:
        raise NotImplementedError

    async def ftms_control(self, value: bytes) -> None:
        raise NotImplementedError


async def test_start_subscribes_to_bike_gearing() -> None:
    """Without this the bike never reports shifts and the gear stream freezes."""

    bike = _GearingBike()
    bridge = WftnpBridge(bike, name="Square Bridge", txt=TXT, advertise=False, port=0)
    await bridge.start()
    try:
        assert bike.gearing_subscriptions == 1
    finally:
        await bridge.stop()


async def test_non_standalone_start_leaves_the_bike_alone() -> None:
    """The dashboard already syncs the bike and pumps its own notifications."""

    bike = _GearingBike()
    bridge = WftnpBridge(
        bike,
        name="Square Bridge",
        txt=TXT,
        advertise=False,
        port=0,
        standalone=False,
    )
    await bridge.start()
    try:
        assert bike.queries == 0
        assert bike.gearing_subscriptions == 0
    finally:
        await bridge.stop()

