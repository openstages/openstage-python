"""FTMS proxy tests: feature bits, transparent forwarding, and relay."""

from __future__ import annotations

import asyncio
import struct
from uuid import UUID

import pytest

from openstage.client import BikeConnectionError
from openstage.wftnp import (
    FEATURE_VALUE,
    FtmsProxy,
    Frame,
    MessageType,
    ResponseCode,
    WftnpServer,
    read_frame,
    uuid_bytes,
)

SERVICE = "00001826-0000-1000-8000-00805f9b34fb"
FEATURE = "00002acc-0000-1000-8000-00805f9b34fb"
DATA = "00002ad2-0000-1000-8000-00805f9b34fb"
CONTROL = "00002ad9-0000-1000-8000-00805f9b34fb"
STATUS = "00002ada-0000-1000-8000-00805f9b34fb"


def test_feature_value_advertises_simulation_and_power_targets() -> None:
    supported, target = struct.unpack("<II", FEATURE_VALUE)
    assert supported == 0
    assert target & (1 << 3), "power target setting"
    assert target & (1 << 13), "indoor bike simulation parameters"


async def _server(writer) -> WftnpServer:
    server = WftnpServer(
        name="Square Test",
        port=0,
        advertise=False,
        txt={"ble-service-uuids": SERVICE},
        services=[FtmsProxy(writer).service],
    )
    await server.start()
    return server


async def _exchange(server: WftnpServer, request: Frame) -> Frame:
    reader, writer = await asyncio.open_connection("127.0.0.1", server.port)
    try:
        writer.write(request.encode())
        await writer.drain()
        return await read_frame(reader)
    finally:
        writer.close()
        await writer.wait_closed()


async def test_control_point_writes_are_forwarded_verbatim() -> None:
    seen: list[bytes] = []

    async def forward(_value: bytes) -> None:
        seen.append(value)

    server = await _server(forward)
    try:
        value = bytes.fromhex("110000000000")
        answer = await _exchange(
            server,
            Frame(MessageType.WRITE_CHARACTERISTIC, 3, body=uuid_bytes(CONTROL) + value),
        )
        assert answer.code == ResponseCode.SUCCESS
        assert answer.body == uuid_bytes(CONTROL)
        assert seen == [value]
    finally:
        await server.stop()


async def test_bike_failure_becomes_write_failed() -> None:
    async def fail(_value: bytes) -> None:
        raise BikeConnectionError("bike is asleep")

    server = await _server(fail)
    try:
        answer = await _exchange(
            server,
            Frame(
                MessageType.WRITE_CHARACTERISTIC,
                4,
                body=uuid_bytes(CONTROL) + bytes.fromhex("0000"),
            ),
        )
        assert answer.code == ResponseCode.WRITE_FAILED
    finally:
        await server.stop()


async def test_status_is_notify_only() -> None:
    async def forward(_value: bytes) -> None:
        return None

    server = await _server(forward)
    try:
        answer = await _exchange(
            server,
            Frame(MessageType.READ_CHARACTERISTIC, 5, body=uuid_bytes(STATUS)),
        )
        assert answer.code == ResponseCode.OPERATION_NOT_SUPPORTED
    finally:
        await server.stop()


async def test_client_relays_indoor_bike_data() -> None:
    wftnp = pytest.importorskip("wftnp")

    async def forward(_value: bytes) -> None:
        return None

    proxy = FtmsProxy(forward)
    server = WftnpServer(
        name="Square Test",
        port=0,
        advertise=False,
        txt={"ble-service-uuids": SERVICE},
        services=[proxy.service],
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
        assert [str(s.uuid) for s in await client.discover_services()] == [SERVICE]
        assert await client.read_characteristic(UUID(FEATURE)) == FEATURE_VALUE
        assert await client.read_characteristic(UUID(DATA)) == bytes(4)

        subscription = await client.subscribe(UUID(DATA))
        try:
            initial = await asyncio.wait_for(anext(subscription), 2)
            assert initial.value == bytes(4)
            proxy.indoor_bike_data = bytes.fromhex("0000c800")
            await server.notify(DATA, proxy.indoor_bike_data)
            note = await asyncio.wait_for(anext(subscription), 2)
            assert note.value == bytes.fromhex("0000c800")
        finally:
            await subscription.close()
    finally:
        await client.stop()
        await server.stop()
