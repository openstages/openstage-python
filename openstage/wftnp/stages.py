"""Stages control service over WFTNP, restricted to the weight write.

The app may set rider and equipment weight, the one operation this bridge
exposes. Every other request the bike supports on the same service stays
unreachable: a write that is not the weight request is refused.

The response value is the bike's own reply, so the app sees exactly what the
bike answered.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from .. import pdu
from ..client import BikeError
from ..uuids import API_REQUEST, API_RESPONSE, API_SERVICE
from .protocol import Property
from .server import GattCharacteristic, GattService, WriteRejected

#: Returns the bike's raw response value for the write.
WeightWriter = Callable[[float, float], Awaitable[bytes]]
Emitter = Callable[[str, bytes], Awaitable[None]]

_WEIGHT_OP = bytes([pdu.Op.WEIGHT, 1])


class StagesControl:
    """The Stages control service with a single allowed operation."""

    def __init__(self, set_weight: WeightWriter, emit: Emitter) -> None:
        self._set_weight = set_weight
        self._emit = emit
        self._service = GattService(
            API_SERVICE,
            (
                GattCharacteristic(API_REQUEST, Property.WRITE, write=self._write),
                GattCharacteristic(API_RESPONSE, Property.NOTIFY),
            ),
        )

    @property
    def service(self) -> GattService:
        return self._service

    async def _write(self, value: bytes) -> None:
        if len(value) != 6 or value[:2] != _WEIGHT_OP:
            raise WriteRejected("only the rider and equipment weight write is allowed")
        user = int.from_bytes(value[2:4], "little") / 100
        equipment = int.from_bytes(value[4:6], "little") / 100
        try:
            answer = await self._set_weight(user, equipment)
        except BikeError as exc:
            raise WriteRejected(str(exc)) from exc
        await self._emit(API_RESPONSE, answer)
