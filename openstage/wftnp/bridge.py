"""Glue between a live SB20 and the WFTNP endpoint.

The bridge is the only module that imports both :mod:`openstage.client` and
:mod:`openstage.wftnp`. Bike notifications are folded through
:class:`openstage.state.BikeState` and mirrored into the WFTNP profiles; app
writes go back through the client, which serialises every outgoing BLE
operation behind one lock, so one operation is in flight at a time and the
rest queue behind it.

Notifications are emitted when a mirrored value changes. The bike repeats
button and brake frames while a control is held; those repeats produce no
change and are dropped, which keeps the Elite stream well under its 10 per
second budget.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping

from .. import pdu
from ..client import (
    BikeError,
    BikeIncompatibleError,
    BikeRadioError,
    BikeSubscriptionError,
    BikeTransientError,
    Notification,
    SB20Client,
)
from ..gearing_data import resync_drivetrain
from ..state import BikeState
from ..uuids import FTMS_CONTROL_POINT, FTMS_INDOOR_BIKE_DATA, FTMS_STATUS
from .elite import (
    BUTTONS,
    LEFT_BRAKE,
    RIGHT_BRAKE,
    STATUS,
    DerailleurGear,
    EliteSquare,
    SequentialGear,
)
from .ftms import FtmsProxy
from .server import DEFAULT_PORT, WftnpServer
from .stages import StagesControl

LOGGER = logging.getLogger(__name__)

#: Minimum gap between auto-repeat edges; the bike repeats a held press about
#: every 60 ms, and the Elite stream must stay under ten notifications a second.
REPEAT_INTERVAL_S = 0.12

#: Identity advertised over mDNS. The app keys button bindings by the MAC, so
#: changing it makes users lose their remap.
DEFAULT_NAME = "Square Bridge"
DEFAULT_SERIAL = "SQ-0001"
DEFAULT_MAC = "02:00:00:00:00:01"


class WftnpBridge:
    """Serve one WFTNP endpoint from one SB20 connection."""

    def __init__(
        self,
        client: SB20Client,
        *,
        name: str,
        txt: Mapping[str, str],
        port: int = DEFAULT_PORT,
        advertise: bool = True,
        standalone: bool = True,
    ) -> None:
        self._client = client
        self._state = BikeState()
        self._elite = EliteSquare()
        self._ftms = FtmsProxy(self._client.ftms_control)
        self._stages = StagesControl(self._set_weight, self._emit)
        self._server = WftnpServer(
            name=name,
            txt=txt,
            port=port,
            advertise=advertise,
            services=[
                self._elite.service,
                self._ftms.service,
                self._stages.service,
            ],
            on_disconnect=self._reset,
        )
        self._standalone = standalone
        self._pump: asyncio.Task[None] | None = None
        self._mask = 0
        self._front_high = 0
        self._rear_high = 0

    @property
    def server(self) -> WftnpServer:
        return self._server

    async def start(self) -> None:
        """Open the endpoint.

        In the default standalone mode the bridge also syncs the bike's RAM
        map with flash, subscribes to gearing and pumps the feed. With
        ``standalone=False`` the caller already did the bike setup and drives
        :meth:`handle` from its own feed loop.
        """

        if self._standalone:
            await self._resync_drivetrain()
            if isinstance(self._client, SB20Client):
                try:
                    await self._client.subscribe_gearing()
                except BikeError as exc:
                    raise BikeSubscriptionError(
                        f"cannot subscribe to bike gearing: {exc}"
                    ) from exc
        await self._server.start()
        if self._standalone:
            self._pump = asyncio.create_task(self._run(), name="wftnp-bike-pump")

    async def stop(self) -> None:
        """Stop mirroring and close the endpoint, attempting both."""

        errors: list[str] = []
        pump, self._pump = self._pump, None
        if pump is not None:
            pump.cancel()
            try:
                await pump
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                errors.append(f"pump: {exc}")
        try:
            await self._server.stop()
        except Exception as exc:
            errors.append(f"server: {exc}")
        if errors:
            raise RuntimeError("; ".join(errors))

    async def _resync_drivetrain(self) -> None:
        """Re-apply the preset stored in flash, as the TUI does on connect.

        Without this the live gear notifications can describe a RAM map that
        differs from flash, and the app would be told the wrong gear counts.
        """

        client = self._client
        if not isinstance(client, SB20Client):
            return
        fragments: list[bytes] = []

        def collect(note: Notification) -> None:
            if isinstance(note.record, pdu.GearMapFragment):
                fragments.append(note.record.data)

        client.add_listener(collect)
        try:
            results = await client.query_all()
        finally:
            client.remove_listener(collect)
        message = await resync_drivetrain(client, self._state, results, fragments)
        if message is not None:
            LOGGER.info("%s", message)

    async def _run(self) -> None:
        while True:
            try:
                async for note in self._client.notifications():
                    try:
                        await self.handle(note)
                    except Exception:
                        LOGGER.exception("cannot forward a bike notification")
                return
            except asyncio.CancelledError:
                raise
            except BikeTransientError as exc:
                LOGGER.warning("bike link lost: %s; reconnecting", exc)
                if not await self._reconnect():
                    return
            except (BikeRadioError, BikeIncompatibleError) as exc:
                LOGGER.error("bike unavailable; bridge keeps stale state: %s", exc)
                return

    async def _reconnect(self) -> bool:
        delay = 1.0
        while True:
            await asyncio.sleep(delay)
            try:
                await self._client.connect()
                if isinstance(self._client, SB20Client):
                    await self._client.subscribe_gearing()
                await self._resync_drivetrain()
                LOGGER.info("bike reconnected; WFTNP endpoint resumed")
                return True
            except (BikeRadioError, BikeIncompatibleError) as exc:
                LOGGER.error("cannot reconnect to the bike: %s", exc)
                return False
            except BikeTransientError as exc:
                LOGGER.warning("reconnect failed: %s; retrying", exc)
                delay = min(30.0, delay * 2.0)
            except BikeError as exc:
                LOGGER.error("cannot reconnect to the bike: %s", exc)
                return False

    async def handle(self, note: Notification) -> None:
        """Mirror one bike notification into the endpoint."""
        self._state.apply(note)
        record = note.record
        if isinstance(record, pdu.ButtonFrame):
            await self._buttons()
        elif isinstance(record, pdu.BrakeFrame):
            await self._brakes(record)
        elif isinstance(record, pdu.GearNotification):
            await self._gear(record)
        elif isinstance(record, pdu.Battery):
            if record.percent is not None:
                percent = max(0, min(100, record.percent))
                if percent != record.percent:
                    LOGGER.info(
                        "clamped out-of-range battery report %s to %s",
                        record.percent,
                        percent,
                    )
                if self._elite.set_battery(percent):
                    await self._emit(STATUS, self._elite.encode_status())
        elif note.stream is pdu.Stream.FTMS_DATA:
            self._ftms.indoor_bike_data = note.raw
            await self._emit(FTMS_INDOOR_BIKE_DATA, note.raw)
        elif note.stream is pdu.Stream.FTMS_STATUS:
            await self._emit(FTMS_STATUS, note.raw)
        elif note.stream is pdu.Stream.FTMS_CONTROL:
            await self._emit(FTMS_CONTROL_POINT, note.raw)

    async def _buttons(self) -> None:
        mask = self._state.pressed_mask()
        if mask == self._mask:
            return
        self._mask = mask
        if self._elite.set_buttons(mask):
            await self._elite_changed()

    async def _brakes(self, record: pdu.BrakeFrame) -> None:
        changed = False
        for button, value in ((LEFT_BRAKE, record.left), (RIGHT_BRAKE, record.right)):
            if value > 0:
                changed = self._elite.press(button) or changed
            else:
                changed = self._elite.release(button) or changed
        if changed:
            await self._elite_changed()

    async def _gear(self, record: pdu.GearNotification) -> None:
        gear = self._elite_gear(record)
        if gear is not None and self._elite.set_gear(gear):
            await self._emit(STATUS, self._elite.encode_status())

    def _elite_gear(
        self, record: pdu.GearNotification
    ) -> SequentialGear | DerailleurGear | None:
        # The stored model header and the live gear notification can disagree:
        # a bike whose flash holds a Dream Drive map may still report a
        # simulated cassette. Trust the header only for its own model, and
        # otherwise keep a high-water mark so the Elite counts never reject a
        # gear the bike reports. Gear indices are 1-based on the wire and in
        # the Elite status, so they pass through unchanged.
        header = self._state.model_header
        try:
            if record.model is pdu.ModelType.COEFFICIENTS:
                total = 0
                if header is not None and header.model is pdu.ModelType.COEFFICIENTS:
                    total = header.rear_count
                self._rear_high = max(self._rear_high, record.rear_index)
                return SequentialGear(
                    total=max(total, self._rear_high),
                    ratio=record.coefficient or 0,
                    current=record.rear_index,
                )
            self._front_high = max(self._front_high, record.front_index)
            self._rear_high = max(self._rear_high, record.rear_index)
            if header is not None and header.model is pdu.ModelType.FRONT_REAR_U8:
                self._front_high = max(self._front_high, header.front_count)
                self._rear_high = max(self._rear_high, header.rear_count)
            return DerailleurGear(
                front_count=self._front_high,
                rear_count=self._rear_high,
                front_teeth=record.front_teeth,
                rear_teeth=record.rear_teeth,
                current_front=record.front_index,
                current_rear=record.rear_index,
            )
        except ValueError as exc:
            LOGGER.info("ignoring an out-of-range gear report: %s", exc)
            return None

    async def _set_weight(self, user_kg: float, equipment_kg: float) -> bytes:
        result = await self._client.set_weight(user_kg, equipment_kg)
        return result.set_response.raw

    async def _reset(self) -> None:
        self._elite.reset()
        self._mask = 0
        self._front_high = 0
        self._rear_high = 0

    async def _elite_changed(self) -> None:
        await self._emit(STATUS, self._elite.encode_status())
        await self._emit(BUTTONS, self._elite.encode_buttons())

    async def _emit(self, characteristic: str, value: bytes) -> None:
        await self._server.notify(characteristic, value)
