"""BLE transport boundary.

The rest of the library talks to :class:`Transport`, never to ``bleak``
directly.  That keeps the protocol testable without a radio and gives the
replay/probe tools one narrow seam to replace.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from bleak import BleakClient, BleakScanner

from . import uuids

NotifyCallback = Callable[[bytes], None]


@runtime_checkable
class Transport(Protocol):
    """Minimal GATT surface used by :class:`openstage.client.SB20Client`."""

    @property
    def is_connected(self) -> bool: ...

    @property
    def mtu_size(self) -> int | None: ...

    @property
    def service_uuids(self) -> frozenset[str]: ...

    async def connect(self) -> None: ...

    async def disconnect(self) -> None: ...

    async def read_characteristic(self, uuid: str) -> bytes: ...

    async def write_characteristic(self, uuid: str, data: bytes) -> None: ...

    async def start_notify(self, uuid: str, callback: NotifyCallback) -> None: ...

    async def stop_notify(self, uuid: str) -> None: ...


@dataclass(frozen=True, slots=True)
class BikeAdvertisement:
    """One advertising device that passed the SB20 filter."""

    name: str
    address: str
    rssi: int | None
    service_uuids: frozenset[str]


def is_sb20_advertisement(name: str, service_uuids: Iterable[str]) -> bool:
    """True for the SB20 main unit.

    The bike advertises FTMS and has a ``Stages Bike <n>`` local name.  A
    Stages power meter also starts with ``Stages`` but does not advertise
    FTMS, so the service UUID is the discriminator.
    """

    advertised = {uuids.normalize(value) for value in service_uuids}
    return (
        name.strip().lower().startswith("stages bike")
        and uuids.normalize(uuids.FITNESS_MACHINE) in advertised
    )


def _bike_from_advertisement(
    device: Any,
    advertisement: Any,
    name_filter: str | None,
) -> BikeAdvertisement | None:
    name = str(
        getattr(device, "name", None)
        or (getattr(advertisement, "local_name", None) if advertisement else None)
        or ""
    ).strip()
    needle = (name_filter or "").lower()
    if needle and needle not in name.lower():
        return None
    advertised = {
        uuids.normalize(str(value))
        for value in (getattr(advertisement, "service_uuids", None) or [])
    }
    if not is_sb20_advertisement(name, advertised):
        return None
    rssi = getattr(advertisement, "rssi", None)
    return BikeAdvertisement(
        name=name,
        address=str(getattr(device, "address", "")),
        rssi=int(rssi) if isinstance(rssi, int) else None,
        service_uuids=frozenset(advertised),
    )


async def _scan_once(
    timeout: float,
    name_filter: str | None,
) -> BikeAdvertisement | None:
    found: BikeAdvertisement | None = None
    ready = asyncio.Event()

    def on_detection(device: Any, advertisement: Any) -> None:
        nonlocal found
        if found is not None:
            return
        candidate = _bike_from_advertisement(device, advertisement, name_filter)
        if candidate is None:
            return
        found = candidate
        ready.set()

    scanner = BleakScanner(detection_callback=on_detection)
    await scanner.start()
    try:
        try:
            await asyncio.wait_for(ready.wait(), timeout=timeout)
        except TimeoutError:
            pass
    finally:
        await scanner.stop()
    return found


async def discover_bikes(
    timeout: float = 5.0,
    name_filter: str | None = None,
) -> list[BikeAdvertisement]:
    """Scan until the first SB20-shaped advertisement appears.

    Each scan attempt lasts ``timeout`` seconds. If no bike is found, scanning
    repeats until one is; the first matching advertisement stops the scan.
    """

    while True:
        found = await _scan_once(timeout, name_filter)
        if found is not None:
            return [found]


class BleakTransport:
    """``Transport`` backed by a ``bleak`` central."""

    def __init__(self, address_or_device: str | Any, *, timeout: float = 30.0) -> None:
        self._client = BleakClient(address_or_device, timeout=timeout)
        self._address = str(getattr(address_or_device, "address", address_or_device))
        self._notify_callbacks: dict[str, NotifyCallback] = {}

    @property
    def address(self) -> str:
        return self._address

    @property
    def is_connected(self) -> bool:
        return bool(self._client.is_connected)

    @property
    def mtu_size(self) -> int | None:
        value = getattr(self._client, "mtu_size", None)
        return int(value) if isinstance(value, int) else None

    @property
    def service_uuids(self) -> frozenset[str]:
        services = getattr(self._client, "services", None)
        if services is None:
            return frozenset()
        try:
            values = services.services.values()
        except AttributeError:
            values = services
        return frozenset(uuids.normalize(str(getattr(service, "uuid", ""))) for service in values)

    async def connect(self) -> None:
        await self._client.connect()

    async def disconnect(self) -> None:
        await self._client.disconnect()

    async def read_characteristic(self, uuid: str) -> bytes:
        return bytes(await self._client.read_gatt_char(uuid))

    async def write_characteristic(self, uuid: str, data: bytes) -> None:
        # The control request characteristic is write-without-response.
        await self._client.write_gatt_char(uuid, data, response=False)

    async def start_notify(self, uuid: str, callback: NotifyCallback) -> None:
        key = uuids.normalize(uuid)

        def on_value(_characteristic: Any, value: bytearray) -> None:
            callback(bytes(value))

        self._notify_callbacks[key] = callback
        await self._client.start_notify(uuid, on_value)

    async def stop_notify(self, uuid: str) -> None:
        key = uuids.normalize(uuid)
        self._notify_callbacks.pop(key, None)
        await self._client.stop_notify(uuid)
