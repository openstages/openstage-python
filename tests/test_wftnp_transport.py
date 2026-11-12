"""WFTNP transport tests.

The raw-socket tests pin the wire format straight to the spec. The client
tests drive the server through ``py-wftnp``, the same code a cycling app
would run, and are skipped when that package is absent.
"""

from __future__ import annotations

import asyncio
import struct
from collections.abc import AsyncIterator
from uuid import UUID

import pytest

from openstage.wftnp import (
    Frame,
    GattCharacteristic,
    GattService,
    MessageType,
    Property,
    ResponseCode,
    WriteRejected,
    WftnpServer,
    read_frame,
    uuid_bytes,
)

SERVICE = "347b0001-7635-408b-8918-8ff3949ce592"
STATUS = "347b0043-7635-408b-8918-8ff3949ce592"
BUTTONS = "347b0045-7635-408b-8918-8ff3949ce592"
CONTROL = "347b0099-7635-408b-8918-8ff3949ce592"
CONTROL_UUID = uuid_bytes(CONTROL)


class _Profile:
    """State the test service reads and writes."""

    def __init__(self) -> None:
        self.status = b"\x01\x01\x57\x00\x00\x00\x00\x00\x0e\x09\xc4\x07"
        self.writes: list[bytes] = []

    async def write(self, value: bytes) -> None:
        if value == b"nope":
            raise WriteRejected
        self.writes.append(value)


def _server(
    profile: _Profile, *, name: str = "Square Test", advertise: bool = False
) -> WftnpServer:
    return WftnpServer(
        name=name,
        port=0,
        advertise=advertise,
        txt={
            "serial-number": "SQ-0001",
            "mac-address": "02:00:00:00:00:01",
            "ble-service-uuids": SERVICE,
        },
        services=[
            GattService(
                SERVICE,
                (
                    GattCharacteristic(
                        STATUS,
                        Property.READ | Property.NOTIFY,
                        read=lambda: profile.status,
                    ),
                    GattCharacteristic(
                        BUTTONS,
                        Property.READ | Property.NOTIFY,
                        read=lambda: b"\x00" * 11,
                    ),
                    GattCharacteristic(CONTROL, Property.WRITE, write=profile.write),
                ),
            )
        ],
    )


@pytest.fixture
async def running() -> AsyncIterator[WftnpServer]:
    profile = _Profile()
    server = _server(profile)
    await server.start()
    try:
        yield server
    finally:
        await server.stop()


async def _exchange(server: WftnpServer, request: Frame) -> Frame:
    reader, writer = await asyncio.open_connection("127.0.0.1", server.port)
    try:
        writer.write(request.encode())
        await writer.drain()
        return await read_frame(reader)
    finally:
        writer.close()
        await writer.wait_closed()


def _request(message_type: MessageType, body: bytes = b"", sequence: int = 7) -> Frame:
    return Frame(message_type=message_type, sequence=sequence, body=body)


async def test_header_is_six_big_endian_bytes() -> None:
    assert Frame(MessageType.ECHO, 0x2A).encode() == bytes.fromhex("01072a000000")


async def test_echo_and_service_discovery(running: WftnpServer) -> None:
    echo = await _exchange(running, _request(MessageType.ECHO, sequence=0x2A))
    assert (echo.message_type, echo.sequence, echo.code) == (MessageType.ECHO, 0x2A, 0)
    assert echo.body == b""

    found = await _exchange(running, _request(MessageType.DISCOVER_SERVICES))
    assert found.code == ResponseCode.SUCCESS
    assert found.body == uuid_bytes(SERVICE)

    chars = await _exchange(
        running, _request(MessageType.DISCOVER_CHARACTERISTICS, uuid_bytes(SERVICE))
    )
    assert chars.body == (
        uuid_bytes(SERVICE)
        + uuid_bytes(STATUS) + bytes([0x05])
        + uuid_bytes(BUTTONS) + bytes([0x05])
        + CONTROL_UUID + bytes([0x02])
    )


async def test_read_write_and_rejection(running: WftnpServer) -> None:
    read = await _exchange(running, _request(MessageType.READ_CHARACTERISTIC, uuid_bytes(STATUS)))
    assert read.code == ResponseCode.SUCCESS
    assert read.body == uuid_bytes(STATUS) + _Profile().status

    accepted = await _exchange(
        running, _request(MessageType.WRITE_CHARACTERISTIC, CONTROL_UUID + b"go")
    )
    assert accepted.code == ResponseCode.SUCCESS
    assert accepted.body == CONTROL_UUID

    rejected = await _exchange(
        running, _request(MessageType.WRITE_CHARACTERISTIC, CONTROL_UUID + b"nope")
    )
    assert rejected.code == ResponseCode.WRITE_FAILED
    assert rejected.body == b""


async def test_error_codes(running: WftnpServer) -> None:
    unknown_type = await _exchange(running, Frame(0x42, 1))
    assert unknown_type.code == ResponseCode.UNKNOWN_MESSAGE_TYPE

    old_version = await _exchange(running, Frame(MessageType.ECHO, 1, version=2))
    assert old_version.code == ResponseCode.UNKNOWN_VERSION

    missing = await _exchange(
        running, _request(MessageType.DISCOVER_CHARACTERISTICS, uuid_bytes(CONTROL))
    )
    assert missing.code == ResponseCode.SERVICE_NOT_FOUND

    unknown_char = await _exchange(
        running,
        _request(MessageType.READ_CHARACTERISTIC, uuid_bytes("347b0099-0000-408b-8918-8ff3949ce592")),
    )
    assert unknown_char.code == ResponseCode.CHARACTERISTIC_NOT_FOUND

    write_only_read = await _exchange(
        running, _request(MessageType.READ_CHARACTERISTIC, CONTROL_UUID)
    )
    assert write_only_read.code == ResponseCode.OPERATION_NOT_SUPPORTED

    read_only_write = await _exchange(
        running, _request(MessageType.WRITE_CHARACTERISTIC, uuid_bytes(STATUS) + b"x")
    )
    assert read_only_write.code == ResponseCode.OPERATION_NOT_SUPPORTED

    notify_on_write_only = await _exchange(
        running, _request(MessageType.ENABLE_NOTIFICATIONS, CONTROL_UUID + b"\x01")
    )
    assert notify_on_write_only.code == ResponseCode.OPERATION_NOT_SUPPORTED

    malformed = await _exchange(
        running, _request(MessageType.DISCOVER_CHARACTERISTICS, b"\x00" * 8)
    )
    assert malformed.code == ResponseCode.UNEXPECTED_ERROR


async def test_notifications_follow_enable_and_disable(running: WftnpServer) -> None:
    reader, writer = await asyncio.open_connection("127.0.0.1", running.port)
    try:
        writer.write(
            _request(MessageType.ENABLE_NOTIFICATIONS, uuid_bytes(STATUS) + b"\x01").encode()
        )
        assert (await read_frame(reader)).code == ResponseCode.SUCCESS
        # Enabling also pushes the current value.
        initial = await asyncio.wait_for(read_frame(reader), 1)
        assert initial.message_type == MessageType.CHARACTERISTIC_NOTIFICATION
        assert initial.body == uuid_bytes(STATUS) + _Profile().status

        await running.notify(STATUS, b"\xaa")
        note = await asyncio.wait_for(read_frame(reader), 1)
        assert note.message_type == MessageType.CHARACTERISTIC_NOTIFICATION
        assert note.body == uuid_bytes(STATUS) + b"\xaa"

        writer.write(
            _request(MessageType.ENABLE_NOTIFICATIONS, uuid_bytes(STATUS) + b"\x00").encode()
        )
        assert (await read_frame(reader)).code == ResponseCode.SUCCESS

        await running.notify(STATUS, b"\xbb")
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(read_frame(reader), 0.2)
    finally:
        writer.close()
        await writer.wait_closed()


async def test_oversized_frame_closes_the_connection(running: WftnpServer) -> None:
    reader, writer = await asyncio.open_connection("127.0.0.1", running.port)
    try:
        writer.write(struct.pack(">BBBBH", 1, 1, 0, 0, 4091))
        await writer.drain()
        assert await asyncio.wait_for(reader.read(), 1) == b""
    finally:
        writer.close()
        await writer.wait_closed()


async def test_client_end_to_end(running: WftnpServer) -> None:
    wftnp = pytest.importorskip("wftnp")
    client = wftnp.WftnpClient(
        wftnp.Endpoint("127.0.0.1", running.port),
        auto_reconnect=False,
        heartbeat_interval=None,
    )
    await client.start()
    try:
        await client.wait_ready(timeout=5)
        services = await client.discover_services()
        assert [str(service.uuid) for service in services] == [SERVICE]

        characteristics = await client.discover_characteristics(UUID(SERVICE))
        assert {str(ch.uuid) for ch in characteristics} == {STATUS, BUTTONS, CONTROL}

        assert await client.read_characteristic(UUID(STATUS)) == _Profile().status

        await client.write_characteristic(UUID(CONTROL), b"go")
        with pytest.raises(wftnp.OperationRejected):
            await client.write_characteristic(UUID(CONTROL), b"nope")

        subscription = await client.subscribe(UUID(STATUS))
        try:
            initial = await asyncio.wait_for(anext(subscription), 2)
            assert initial.value == _Profile().status
            await running.notify(STATUS, b"\xcc")
            note = await asyncio.wait_for(anext(subscription), 2)
            assert note.value == b"\xcc"
        finally:
            await subscription.close()
    finally:
        await client.stop()


async def test_new_client_replaces_the_old_one(running: WftnpServer) -> None:
    first_reader, first_writer = await asyncio.open_connection("127.0.0.1", running.port)
    try:
        second_reader, second_writer = await asyncio.open_connection("127.0.0.1", running.port)
        try:
            second_writer.write(_request(MessageType.ECHO, sequence=9).encode())
            await second_writer.drain()
            answer = await read_frame(second_reader)
            assert answer.sequence == 9
            assert await asyncio.wait_for(first_reader.read(), 1) == b""
        finally:
            second_writer.close()
            await second_writer.wait_closed()
    finally:
        first_writer.close()
        await first_writer.wait_closed()


async def test_advertisement_is_discoverable() -> None:
    pytest.importorskip("wftnp")
    from wftnp import discovery

    profile = _Profile()
    server = _server(profile, name="Square Transport Test", advertise=True)
    await server.start()
    try:
        advertisements = await discovery.discover(timeout=3)
    finally:
        await server.stop()

    matching = [adv for adv in advertisements if adv.name.startswith("Square Transport Test")]
    assert matching, advertisements
    advertisement = matching[0]
    assert advertisement.endpoint.port == server.port
    assert str(advertisement.services[0]) == SERVICE
    assert advertisement.serial_number == "SQ-0001"
