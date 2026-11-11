"""Replay a captured session without a radio.

The replay client implements the same feed surface as
:class:`openstage.client.SB20Client`: the dashboard consumes decoded
notifications and does not care whether they came from BLE or JSON.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncGenerator
from pathlib import Path

from . import pdu
from .capture import Capture, load_capture
from .client import BikeIdentity, Notification
from .pdu import Stream


class ReplayClient:
    """Feed a :class:`Capture` as notifications."""

    def __init__(
        self,
        capture: Capture | str | Path,
        *,
        speed: float = 1.0,
        loop: bool = False,
    ) -> None:
        self._capture = capture if isinstance(capture, Capture) else load_capture(capture)
        self._speed = max(speed, 0.05)
        self._loop = loop
        self._status = "replay not started"
        self._closed = False

    @property
    def mode(self) -> str:
        return "replay"

    @property
    def status(self) -> str:
        return self._status

    @property
    def identity(self) -> BikeIdentity:
        return self._capture.identity

    @property
    def mtu(self) -> int | None:
        return self._capture.mtu

    @property
    def capture(self) -> Capture:
        return self._capture

    async def connect(self) -> None:
        self._status = (
            f"replay {len(self._capture.frames)} frames, "
            f"{len(self._capture.exchanges)} exchanges, {self._speed:g}×"
        )

    async def close(self) -> None:
        self._closed = True
        self._status = "replay stopped"

    async def query_all(self) -> dict[str, pdu.Pdu | Exception]:
        return {}

    async def notifications(self) -> AsyncGenerator[Notification, None]:
        # Query responses first, then telemetry in recorded order.
        for exchange in self._capture.exchanges:
            for raw in exchange.responses:
                if self._closed:
                    return
                yield Notification(
                    stream=Stream.API,
                    raw=raw,
                    record=pdu.decode_pdu(raw),
                    t=time.monotonic(),
                )

        while True:
            previous = 0.0
            for frame in self._capture.frames:
                if self._closed:
                    return
                delay = max(0.0, frame.t - previous) / self._speed
                if delay:
                    await asyncio.sleep(delay)
                previous = frame.t
                yield Notification(
                    stream=frame.stream,
                    raw=frame.raw,
                    record=pdu.decode_notification(frame.stream, frame.raw),
                    t=time.monotonic(),
                )
            if not self._loop:
                self._status = "replay finished"
                return
            await asyncio.sleep(0.5)
