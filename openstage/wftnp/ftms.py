"""Fitness Machine Service over WFTNP, as a proxy to the bike.

The service is byte-transparent. Indoor bike data comes from the bike and is
forwarded unchanged; control point writes go to the bike as they arrive and
its indications come back through the same characteristic. FTMS procedure
semantics stay on the bike, so every procedure it implements remains
reachable without this module knowing the op codes.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from ..client import BikeError
from ..pdu import SafetyError
from ..uuids import (
    FITNESS_MACHINE,
    FTMS_CONTROL_POINT,
    FTMS_FEATURE,
    FTMS_INDOOR_BIKE_DATA,
    FTMS_STATUS,
)
from .protocol import Property
from .server import GattCharacteristic, GattService, WriteRejected

ControlWriter = Callable[[bytes], Awaitable[None]]

#: Supported features (none) and target-setting features: power target (bit 3)
#: and indoor bike simulation parameters (bit 13), each little-endian.
FEATURE_VALUE = bytes.fromhex("0000000008200000")

#: A valid frame with every optional Indoor Bike Data field absent.
IDLE_INDOOR_BIKE_DATA = bytes(4)


class FtmsProxy:
    """The Fitness Machine Service, forwarding bytes to and from the bike."""

    def __init__(self, write_control: ControlWriter) -> None:
        self.indoor_bike_data = IDLE_INDOOR_BIKE_DATA
        self._write_control = write_control
        self._service = GattService(
            FITNESS_MACHINE,
            (
                GattCharacteristic(
                    FTMS_FEATURE, Property.READ, read=lambda: FEATURE_VALUE
                ),
                GattCharacteristic(
                    FTMS_INDOOR_BIKE_DATA,
                    Property.READ | Property.NOTIFY,
                    read=lambda: self.indoor_bike_data,
                ),
                GattCharacteristic(FTMS_STATUS, Property.NOTIFY),
                GattCharacteristic(
                    FTMS_CONTROL_POINT,
                    Property.WRITE | Property.NOTIFY,
                    write=self._write,
                ),
            ),
        )

    @property
    def service(self) -> GattService:
        return self._service

    async def _write(self, value: bytes) -> None:
        try:
            await self._write_control(value)
        except (BikeError, SafetyError) as exc:
            raise WriteRejected(str(exc)) from exc
