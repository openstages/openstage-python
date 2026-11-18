"""High-level SB20 client.

The client owns the transport and the notification streams, and allows one
outstanding request per connection. Read requests use typed builders and an
exact allow-list. Configuration writes use explicit builders and pass
``pdu.ensure_write_allowed`` before they reach the transport.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncGenerator, Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from enum import Enum
from typing import Any, Literal, Protocol, TypeVar

from . import pdu, uuids
from .pdu import ReadRequest, StoreSettings, StoreSettingsCommand, Stream, WriteRequest
from .transport import BleakTransport, BikeAdvertisement, Transport, discover_bikes


class ControlMode(Enum):
    """The SB20's three control modes; sending a target selects the mode."""

    ERG = "erg"
    RESISTANCE = "resistance"
    SIMULATION = "simulation"


def _merge_zero_reset(records: Sequence[pdu.ZeroReset]) -> pdu.ZeroReset:
    if not records:
        raise ValueError("no zero-reset records")
    last = records[-1]
    left = next((item.left_offset for item in records if item.left_offset is not None), None)
    right = next((item.right_offset for item in records if item.right_offset is not None), None)
    manufacturer = next(
        (item.manufacturer_code for item in records if item.manufacturer_code is not None),
        None,
    )
    company = next((item.company_id for item in records if item.company_id is not None), None)
    torque = next((item.torque_offset for item in records if item.torque_offset is not None), None)
    return replace(
        last,
        left_offset=left,
        right_offset=right,
        manufacturer_code=manufacturer,
        company_id=company,
        torque_offset=torque,
        valid_mask=last.valid_mask
        | (0x01 if left is not None else 0)
        | (0x02 if right is not None else 0)
        | (0x04 if manufacturer is not None else 0)
        | (0x08 if company is not None else 0)
        | (0x10 if torque is not None else 0),
    )


class BikeError(RuntimeError):
    """Base error for the library."""


class BikeNotFound(BikeError):
    """No SB20-shaped device was found, or the control service is absent."""


class BikeIncompatibleError(BikeError):
    """The connected device does not expose the required SB20 surface."""


class BikeConnectionError(BikeError):
    """The transport is not connected or a write/read failed."""


class BikeRadioError(BikeConnectionError):
    """The Bluetooth adapter is unavailable, disabled or not permitted."""


class BikeTransientError(BikeConnectionError):
    """A connection failure that a fresh connection may recover from."""


class BikeDisconnected(BikeTransientError):
    """The BLE link dropped after a successful connection."""


class BikeSubscriptionError(BikeTransientError):
    """A required notification stream could not be enabled."""


class BikeTimeout(BikeError):
    """The bike did not answer an allow-listed request in time."""


class ZeroResetTimeout(BikeTimeout):
    """Zero reset timed out after at least one side reported a result."""

    def __init__(self, message: str, result: pdu.ZeroReset) -> None:
        super().__init__(message)
        self.result = result


class BikeProtocolError(BikeError):
    """The bike answered with an error PDU or an unexpected record."""


class BikePermissionError(BikeProtocolError):
    """The client-defined ``0xFA`` permission-denied PDU was received."""


class BikeAlreadyConnectedError(BikeProtocolError):
    """The client-defined ``0xFB`` already-connected PDU was received."""


@dataclass(frozen=True, slots=True)
class BikeIdentity:
    """Device Information values plus the advertising identity."""

    name: str | None = None
    address: str | None = None
    model: str | None = None
    serial: str | None = None
    firmware: str | None = None
    hardware: str | None = None
    software: str | None = None
    manufacturer: str | None = None

    def as_dict(self) -> dict[str, str | None]:
        return {
            "name": self.name,
            "address": self.address,
            "model": self.model,
            "serial": self.serial,
            "firmware": self.firmware,
            "hardware": self.hardware,
            "software": self.software,
            "manufacturer": self.manufacturer,
        }


@dataclass(frozen=True, slots=True)
class Notification:
    """One decoded notification, with the stream it arrived on."""

    stream: Stream
    raw: bytes
    record: pdu.Pdu
    t: float

    @property
    def hex(self) -> str:
        return self.raw.hex()


@dataclass(slots=True)
class _Pending:
    request: ReadRequest | WriteRequest
    future: asyncio.Future[pdu.Pdu]


@dataclass(slots=True)
class _StoreWaiter:
    started: float
    future: asyncio.Future[tuple[StoreSettings, ...]]
    messages: list[StoreSettings]


@dataclass(slots=True)
class _GearStoreWaiter:
    started: float
    future: asyncio.Future[pdu.GearTransferAck]


@dataclass(frozen=True, slots=True)
class StoreResult:
    """Responses observed for one ``0xFD/0`` operation."""

    messages: tuple[StoreSettings, ...]

    @property
    def command(self) -> StoreSettingsCommand | None:
        if not self.messages:
            return None
        return self.messages[-1].status

    @property
    def stored(self) -> bool:
        return any(message.stored for message in self.messages)

    @property
    def busy(self) -> bool:
        return any(message.status is StoreSettingsCommand.ERROR for message in self.messages)


@dataclass(frozen=True, slots=True)
class WriteResult:
    """One configuration Set, optionally followed by ``0xFD/0``."""

    set_response: pdu.Pdu
    store: StoreResult | None = None

    @property
    def persisted(self) -> bool:
        return self.store is not None and self.store.stored


class SettingsCommitError(BikeError):
    """A settings commit stopped before completing; device state is unknown."""

    def __init__(
        self,
        step: str,
        completed: tuple[WriteResult, ...] = (),
        message: str | None = None,
    ) -> None:
        self.step = step
        self.completed = completed
        detail = message or f"{step} failed"
        super().__init__(
            f"{detail}; {len(completed)} earlier write(s) may have been applied"
        )


@dataclass(frozen=True, slots=True)
class SettingsCommit:
    """Result of one fluent settings commit."""

    operations: tuple[WriteResult, ...]
    store: StoreResult | None = None

    @property
    def writes(self) -> int:
        return len(self.operations)

    @property
    def persisted(self) -> bool:
        return self.store is not None and self.store.stored


_GEARING_FRAGMENT_GAP_S = 0.02
_CLEANUP_TIMEOUT_S = 5.0
_LOGGER = logging.getLogger(__name__)

_API_CHARACTERISTICS = frozenset({uuids.API_REQUEST, uuids.API_RESPONSE})
_BUTTON_CHARACTERISTICS = frozenset({uuids.BUTTON_ACTIVE, uuids.BRAKE_VALUE})
_FTMS_CHARACTERISTICS = frozenset(
    {
        uuids.FTMS_FEATURE,
        uuids.FTMS_INDOOR_BIKE_DATA,
        uuids.FTMS_STATUS,
        uuids.FTMS_CONTROL_POINT,
    }
)
_CSC_CHARACTERISTIC = uuids.CSC_MEASUREMENT


def _is_radio_error(exc: BaseException) -> bool:
    """Best-effort classification of BLE adapter failures."""

    name = type(exc).__name__.lower()
    text = str(exc).lower()
    if "devicenotfound" in name or ("device" in text and "not found" in text):
        return False
    markers = (
        "adapter",
        "not ready",
        "notready",
        "not authorized",
        "permission",
        "powered off",
        "radio",
    )
    return any(marker in text for marker in markers)


class DrivetrainMode(Enum):
    """The two drivetrain models the SB20 accepts."""

    DREAM_DRIVE = "dream_drive"
    SIMULATED_CASSETTE = "simulated_cassette"


@dataclass(frozen=True, slots=True)
class GearPlan:
    """A fully encoded drivetrain transfer, before any BLE traffic."""

    mode: DrivetrainMode
    model: pdu.ModelType
    front_count: int
    rear_count: int
    dream_drive: bool
    step: int
    front_gears: tuple[int, ...]
    cassette_gears: tuple[int, ...]
    coefficients: tuple[int, ...]
    map_bytes: bytes
    crc8: int
    header: WriteRequest
    fragments: tuple[WriteRequest, ...]
    apply: WriteRequest

    @property
    def map_size(self) -> int:
        return len(self.map_bytes)

    @property
    def total_gears(self) -> int:
        if self.dream_drive:
            return self.rear_count
        return self.front_count * self.rear_count

    @property
    def header_pdu(self) -> bytes:
        return self.header.pdu

    def as_header(self) -> pdu.GearModelHeader:
        """Synthetic header for the state view when readback is unavailable."""

        return pdu.GearModelHeader(
            raw=self.header.pdu,
            op=pdu.Op.GEARING,
            command=pdu.GearingTransferCommand.GET_CURRENT_MODEL_SUCCESS,
            model=self.model,
            front_count=self.front_count,
            rear_count=self.rear_count,
            crc8=self.crc8,
            dream_drive=self.dream_drive,
            dream_drive_step=self.step,
            map_size=self.map_size,
        )

    def matches(self, header: pdu.GearModelHeader | None) -> bool:
        if header is None:
            return False
        if (
            header.model is not self.model
            or header.front_count != self.front_count
            or header.rear_count != self.rear_count
            or header.crc8 != self.crc8
            or header.dream_drive != self.dream_drive
            or header.map_size != self.map_size
        ):
            return False
        # The step octet is ignored for Model Type 0; only Dream Drive uses it.
        return not self.dream_drive or header.dream_drive_step == self.step

    def matches_gear(self, gear: pdu.GearNotification | None) -> bool:
        """True when a current-gear notification belongs to this map."""

        if gear is None or gear.model is not self.model:
            return False
        if not 1 <= gear.front_index <= self.front_count:
            return False
        if not 1 <= gear.rear_index <= self.rear_count:
            return False
        if self.dream_drive:
            return gear.coefficient == self.coefficients[gear.rear_index - 1]
        return (
            gear.front_teeth == self.front_gears[gear.front_index - 1]
            and gear.rear_teeth == self.cassette_gears[gear.rear_index - 1]
        )


@dataclass(frozen=True, slots=True)
class GearStoreResult:
    """Outcome of one gearing STORE (flash write).

    ``header_after`` is the stored header re-read after a successful store
    (``0C 02 04``); it is ``None`` for an unconfirmed or failed store.
    """

    ack: pdu.GearTransferAck | None
    error: str | None
    header_after: pdu.GearModelHeader | None = None

    @property
    def stored(self) -> bool:
        return (
            self.ack is not None
            and self.ack.command == pdu.GearingTransferCommand.STORE_SUCCESS
        )


@dataclass(frozen=True, slots=True)
class GearTransferResult:
    """Outcome of the RAM-only drivetrain update (ERG off + apply)."""

    plan: GearPlan
    request_ack: pdu.GearTransferAck
    apply_ack: pdu.GearTransferAck


class GearSettings:
    """Fluent drivetrain builder.

    Example::

        await client.gears() \
            .set_mode(DrivetrainMode.DREAM_DRIVE) \
            .set_gears_number(24) \
            .set_front_shift(2) \
            .commit()

        await client.gears() \
            .set_mode(DrivetrainMode.SIMULATED_CASSETTE) \
            .set_front_gears([48, 32]) \
            .set_cassette_gears([10, 11, 12, 13, 14, 15, 17, 19, 21, 24, 28, 33]) \
            .commit()
    """

    def __init__(self, client: SB20Client) -> None:
        self._client = client
        self._mode = DrivetrainMode.DREAM_DRIVE
        self._gears_number: int | None = None
        self._front_shift = 2
        self._front_gears: tuple[int, ...] | None = None
        self._cassette_gears: tuple[int, ...] | None = None

    def set_mode(self, mode: DrivetrainMode) -> GearSettings:
        self._mode = mode
        return self

    def set_gears_number(self, count: int) -> GearSettings:
        if not pdu.DREAM_DRIVE_GEARS_MIN <= count <= pdu.DREAM_DRIVE_GEARS_MAX:
            raise ValueError(
                f"Dream Drive gears must be {pdu.DREAM_DRIVE_GEARS_MIN}.."
                f"{pdu.DREAM_DRIVE_GEARS_MAX}: {count!r}"
            )
        self._gears_number = count
        return self

    def set_front_shift(self, step: int) -> GearSettings:
        if not pdu.DREAM_DRIVE_STEP_MIN <= step <= pdu.DREAM_DRIVE_STEP_MAX:
            raise ValueError(
                f"Dream Drive step must be {pdu.DREAM_DRIVE_STEP_MIN}.."
                f"{pdu.DREAM_DRIVE_STEP_MAX}: {step!r}"
            )
        self._front_shift = step
        return self

    def set_front_gears(self, values: Sequence[int]) -> GearSettings:
        self._front_gears = pdu.sorted_front_gears(values)
        return self

    def set_cassette_gears(self, values: Sequence[int]) -> GearSettings:
        self._cassette_gears = pdu.sorted_cassette_gears(values)
        return self

    def plan(self) -> GearPlan:
        """Build the map and PDUs without touching the bike."""

        if self._mode is DrivetrainMode.DREAM_DRIVE:
            if self._gears_number is None:
                raise ValueError("Dream Drive gear count is not set")
            coefficients = pdu.dream_drive_coefficients(self._gears_number)
            map_bytes = pdu.build_dream_drive_map(self._gears_number)
            model = pdu.ModelType.COEFFICIENTS
            front_count = 1
            rear_count = self._gears_number
            dream_drive = True
            step = self._front_shift
            front_gears: tuple[int, ...] = ()
            cassette_gears: tuple[int, ...] = ()
        else:
            if self._front_gears is None or self._cassette_gears is None:
                raise ValueError("front gears and cassette gears are required")
            front_gears = self._front_gears
            cassette_gears = self._cassette_gears
            map_bytes = bytes(front_gears) + bytes(cassette_gears)
            coefficients = ()
            model = pdu.ModelType.FRONT_REAR_U8
            front_count = len(front_gears)
            rear_count = len(cassette_gears)
            dream_drive = False
            # The bike reports step 4 for cassette models, so copy that value;
            # the step octet has no known role for this model.
            step = 4
        crc8 = pdu.crc8(map_bytes)
        header = pdu.build_gearing_header(
            model=model,
            front_count=front_count,
            rear_count=rear_count,
            crc8=crc8,
            dream_drive=dream_drive,
            step=step,
            map_size=len(map_bytes),
        )
        fragments = tuple(
            pdu.build_gearing_fragment(chunk)
            for chunk in pdu.split_gearing_map(map_bytes)
        )
        return GearPlan(
            mode=self._mode,
            model=model,
            front_count=front_count,
            rear_count=rear_count,
            dream_drive=dream_drive,
            step=step,
            front_gears=front_gears,
            cassette_gears=cassette_gears,
            coefficients=coefficients,
            map_bytes=map_bytes,
            crc8=crc8,
            header=header,
            fragments=fragments,
            apply=pdu.build_gearing_apply(header.pdu),
        )

    async def commit(self) -> GearTransferResult:
        """Apply the map to RAM only: ERG off, then request → fragments → APPLY.

        This is not persistent across a power cycle; call
        :meth:`SB20Client.store_gearing` to write the applied map to flash.
        """

        plan = self.plan()
        await self._client.erg_off()
        request_ack, apply_ack = await self._client._apply_gearing(plan)
        return GearTransferResult(
            plan=plan,
            request_ack=request_ack,
            apply_ack=apply_ack,
        )


class BikeSettings:
    """Fluent, diff-only settings builder.

    Example::

        await client.settings() \
            .set_weight(85.0, 7.5) \
            .set_brake(False) \
            .set_button_slots({1: pdu.ButtonAction.REAR_UP}) \
            .commit()

    Setters only record intent. ``commit`` reads the current bike values,
    compares, and sends only operations that actually change something. Flash
    storage is opt-in with ``store=True``.
    """

    def __init__(self, client: SB20Client) -> None:
        self._client = client
        self._weight: tuple[float, float] | None = None
        self._brake: bool | None = None
        self._dual: bool | None = None
        self._gradient: tuple[int, bool] | None = None
        self._crank_mm: float | None = None
        self._button_wire: tuple[int, ...] | None = None
        self._button_actions: tuple[pdu.ButtonAction, ...] | None = None
        self._button_changes: dict[int, pdu.ButtonAction] = {}

    def set_weight(self, user_kg: float, equipment_kg: float) -> BikeSettings:
        self._weight = (user_kg, equipment_kg)
        return self

    def set_brake(self, enabled: bool) -> BikeSettings:
        self._brake = enabled
        return self

    def set_parameters(
        self,
        brake_enabled: bool | None,
        dual_ble_enabled: bool | None,
    ) -> BikeSettings:
        self._brake = brake_enabled
        self._dual = dual_ble_enabled
        return self

    def set_gradient_scaling(self, factor: int, delay_enabled: bool = False) -> BikeSettings:
        self._gradient = (factor, delay_enabled)
        return self

    def set_crank_length(self, mm: float) -> BikeSettings:
        self._crank_mm = mm
        return self

    def set_button_actions(
        self,
        actions: Sequence[pdu.ButtonAction],
    ) -> BikeSettings:
        self._button_actions = tuple(actions)
        self._button_wire = None
        self._button_changes.clear()
        return self

    def set_button_slots(
        self,
        changes: Mapping[int, pdu.ButtonAction],
    ) -> BikeSettings:
        self._button_changes.update(changes)
        return self

    def set_buttons(self, values: Sequence[int]) -> BikeSettings:
        self._button_wire = tuple(values)
        self._button_actions = None
        self._button_changes.clear()
        return self

    def _desired_buttons(self, current: Sequence[int]) -> tuple[int, ...]:
        if self._button_wire is not None:
            values = list(self._button_wire)
        elif self._button_actions is not None:
            values = [int(action) for action in self._button_actions] + [0]
        else:
            values = list(current)
        values.extend([0] * (len(pdu.BUTTON_WIRE_SLOTS) - len(values)))
        for physical, action in self._button_changes.items():
            if not 1 <= physical <= len(pdu.BUTTON_MASK_SLOTS):
                raise ValueError(
                    f"physical button must be 1…{len(pdu.BUTTON_MASK_SLOTS)}: {physical}"
                )
            values[physical - 1] = int(action)
        values[-1] = 0
        return tuple(values)

    async def commit(self, *, store: bool = False) -> SettingsCommit:
        results: list[WriteResult] = []

        async def run(label: str, operation: Awaitable[Any]) -> Any:
            try:
                return await operation
            except (BikeError, ValueError) as exc:
                raise SettingsCommitError(label, tuple(results), str(exc)) from exc

        if self._weight is not None:
            current_weight = await run("read weight", self._client.get_weight())
            user, equipment = self._weight
            if (
                abs(user - current_weight.user_kg) > 0.005
                or abs(equipment - current_weight.equipment_kg) > 0.005
            ):
                results.append(
                    await run("set weight", self._client.set_weight(user, equipment))
                )

        if self._brake is not None or self._dual is not None:
            current_parameters = await run(
                "read parameters", self._client.get_parameters()
            )
            brake_changed = (
                self._brake is not None
                and current_parameters.brake_enabled != self._brake
            )
            dual_changed = (
                self._dual is not None
                and current_parameters.dual_ble_enabled != self._dual
            )
            if brake_changed or dual_changed:
                results.append(
                    await run(
                        "set parameters",
                        self._client.set_parameters(self._brake, self._dual),
                    )
                )

        if self._gradient is not None:
            current_gradient = await run(
                "read gradient scaling", self._client.get_gradient_scaling()
            )
            factor, delay = self._gradient
            if (
                current_gradient.factor != factor
                or current_gradient.delay_enabled != delay
            ):
                results.append(
                    await run(
                        "set gradient scaling",
                        self._client.set_gradient_scaling(factor, delay),
                    )
                )

        if self._crank_mm is not None:
            current_pairing = await run(
                "read crank pairing", self._client.get_pairing()
            )
            if (
                current_pairing.crank_length_mm is None
                or abs(current_pairing.crank_length_mm - self._crank_mm) > 0.01
            ):
                results.append(
                    await run(
                        "set crank length",
                        self._client.set_crank_length(self._crank_mm),
                    )
                )

        if (
            self._button_wire is not None
            or self._button_actions is not None
            or self._button_changes
        ):
            current_buttons = await run("read buttons", self._client.get_buttons())
            desired = self._desired_buttons(current_buttons.values)
            if desired != current_buttons.values:
                results.append(
                    await run("set buttons", self._client.set_buttons(desired))
                )

        if store and results:
            store_result = await run("store settings", self._client.store_settings())
        else:
            store_result = None
        self._weight = None
        self._brake = None
        self._dual = None
        self._gradient = None
        self._crank_mm = None
        self._button_wire = None
        self._button_actions = None
        self._button_changes.clear()
        return SettingsCommit(tuple(results), store_result)


T = TypeVar("T", bound=pdu.Pdu)


class BikeFeed(Protocol):
    """What the dashboard and probe need from a live or replayed session."""

    @property
    def mode(self) -> str: ...

    @property
    def status(self) -> str: ...

    @property
    def identity(self) -> BikeIdentity: ...

    @property
    def mtu(self) -> int | None: ...

    async def connect(self) -> None: ...

    async def close(self) -> None: ...

    def notifications(self) -> AsyncGenerator[Notification, None]: ...

    async def query_all(self) -> dict[str, pdu.Pdu | Exception]: ...


class SB20Client:
    """Client for a Stages SB20: read queries plus explicit allow-listed writes."""

    def __init__(
        self,
        *,
        transport: Transport | None = None,
        address: str | None = None,
        name_filter: str | None = None,
        scan_timeout: float = 5.0,
        request_timeout: float = 5.0,
        store_timeout: float = 10.0,
        gearing_store_timeout: float = 240.0,
        gearing_apply_timeout: float = 30.0,
        zero_reset_timeout: float = 30.0,
        subscribe_buttons: bool = True,
        subscribe_ftms: bool = True,
        subscribe_csc: bool = True,
    ) -> None:
        self._transport = transport
        self._owns_transport = transport is None
        self._address = address
        self._name_filter = name_filter
        self._scan_timeout = scan_timeout
        self._request_timeout = request_timeout
        self._store_timeout = store_timeout
        self._gearing_store_timeout = gearing_store_timeout
        self._gearing_apply_timeout = gearing_apply_timeout
        self._zero_reset_timeout = zero_reset_timeout
        self._subscribe_buttons = subscribe_buttons
        self._subscribe_ftms = subscribe_ftms
        self._subscribe_csc = subscribe_csc

        self._identity = BikeIdentity(address=address)
        self._mtu: int | None = None
        self._status = "not connected"
        self._mode = "live"
        self._notes: list[str] = []
        self._listeners: list[Callable[[Notification], None]] = []
        self._queue: asyncio.Queue[Notification | None] = asyncio.Queue()
        self._started: dict[Stream, str] = {}
        self._pending: _Pending | None = None
        self._store_waiter: _StoreWaiter | None = None
        self._gear_store_waiter: _GearStoreWaiter | None = None
        self._request_lock = asyncio.Lock()
        self._telemetry: set[str] = set()
        self._closed = False
        self._closing = False
        self._disconnect_reason: BikeDisconnected | None = None
        self._csc_available = True
        self._cleanup_errors: tuple[str, ...] = ()

    # ------------------------------------------------------------------ properties

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def status(self) -> str:
        return self._status

    @property
    def identity(self) -> BikeIdentity:
        return self._identity

    @property
    def mtu(self) -> int | None:
        return self._mtu

    @property
    def notes(self) -> tuple[str, ...]:
        return tuple(self._notes)

    @property
    def transport(self) -> Transport | None:
        return self._transport

    @property
    def csc_available(self) -> bool:
        return self._csc_available

    @property
    def cleanup_errors(self) -> tuple[str, ...]:
        return self._cleanup_errors

    # ------------------------------------------------------------------ lifecycle

    async def connect(self) -> None:
        """Discover (if needed), connect, verify the required GATT surface and subscribe."""

        if self._closed:
            raise BikeConnectionError("client is closed; create a new SB20Client")
        if self._transport is not None and self._transport.is_connected:
            return

        self._disconnect_reason = None
        self._csc_available = True
        self._cleanup_errors = ()
        self._queue = asyncio.Queue()
        self._started.clear()
        self._telemetry.clear()

        if self._owns_transport:
            self._transport = None

        if self._transport is None:
            address = self._address
            name: str | None = None
            if address is None:
                self._status = (
                    f"scanning for a Stages bike "
                    f"({self._scan_timeout:.0f}s per attempt; retrying)…"
                )
                try:
                    candidates = await discover_bikes(self._scan_timeout, self._name_filter)
                except Exception as exc:
                    self._status = "Bluetooth scan failed"
                    raise BikeRadioError(f"Bluetooth scan failed: {exc}") from exc
                chosen: BikeAdvertisement = candidates[0]
                address = chosen.address
                name = chosen.name
                self._address = address
                self._note(f"selected {name!r} ({address})")
            try:
                self._transport = BleakTransport(
                    address,
                    disconnected_callback=self._on_transport_disconnect,
                )
            except Exception as exc:
                self._status = "connection failed"
                raise BikeConnectionError(
                    f"cannot create BLE client for {address}: {exc}"
                ) from exc
            self._identity = replace(
                self._identity, address=address, name=name or self._identity.name
            )
        else:
            setter = getattr(self._transport, "set_disconnected_callback", None)
            if setter is not None:
                setter(self._on_transport_disconnect)

        self._status = f"connecting to {self._identity.address or 'device'}…"
        try:
            await self._transport.connect()
        except Exception as exc:
            self._status = "connection failed"
            if _is_radio_error(exc):
                raise BikeRadioError(f"Bluetooth connection failed: {exc}") from exc
            raise BikeTransientError(f"connect failed: {exc}") from exc

        try:
            self._verify_surface()
            self._mtu = self._transport.mtu_size
            await self._read_identity()
            await self._start_notifications()
        except Exception:
            await self._disconnect_best_effort()
            raise

        self._status = f"connected to {self._identity.name or self._identity.address or 'SB20'}"
        self._note(f"connected; MTU {self._mtu if self._mtu is not None else 'unknown'}")

    def _verify_surface(self) -> None:
        """Reject a device that does not expose the required SB20 GATT surface."""

        transport = self._transport
        assert transport is not None
        services = {uuids.normalize(value) for value in transport.service_uuids}
        missing: list[str] = []
        if uuids.normalize(uuids.API_SERVICE) not in services:
            missing.append(uuids.API_SERVICE)
        if self._subscribe_buttons and uuids.normalize(uuids.BUTTON_SERVICE) not in services:
            missing.append(uuids.BUTTON_SERVICE)
        if self._subscribe_ftms and uuids.normalize(uuids.FITNESS_MACHINE) not in services:
            missing.append(uuids.FITNESS_MACHINE)

        characteristics = getattr(transport, "characteristic_uuids", None)
        if characteristics is not None:
            available = {uuids.normalize(value) for value in characteristics}
            required = set(_API_CHARACTERISTICS)
            if self._subscribe_buttons:
                required.update(_BUTTON_CHARACTERISTICS)
            if self._subscribe_ftms:
                required.update(_FTMS_CHARACTERISTICS)
            missing.extend(
                value for value in sorted(required) if uuids.normalize(value) not in available
            )

        if missing:
            names = ", ".join(sorted(set(missing)))
            raise BikeIncompatibleError(
                f"connected device is missing required services/characteristics: {names}"
            )

        if self._subscribe_csc:
            service_present = (
                uuids.normalize(uuids.CYCLING_SPEED_AND_CADENCE) in services
            )
            characteristic_present = (
                characteristics is None
                or uuids.normalize(_CSC_CHARACTERISTIC)
                in {uuids.normalize(value) for value in characteristics}
            )
            if not service_present or not characteristic_present:
                self._csc_available = False
                self._note("CSC unavailable; cadence falls back to API/FTMS")

    async def close(self) -> None:
        """Close telemetry, notifications and the transport.  Safe to call twice."""

        if self._closed:
            return
        self._closing = True
        if self._store_waiter is not None and not self._store_waiter.future.done():
            self._store_waiter.future.cancel()
        self._store_waiter = None
        if self._gear_store_waiter is not None and not self._gear_store_waiter.future.done():
            self._gear_store_waiter.future.cancel()
        self._gear_store_waiter = None
        self._disconnect_reason = None
        errors: list[str] = []
        try:
            if self._telemetry:
                try:
                    await asyncio.wait_for(self.close_telemetry(), timeout=2.0)
                except Exception as exc:
                    errors.append(f"telemetry: {exc}")
            errors.extend(await self._release_transport())
        finally:
            self._telemetry.clear()
            self._started.clear()
            self._status = "disconnected"
            self._queue.put_nowait(None)
            self._closed = True
            self._closing = False
            self._cleanup_errors = tuple(errors)
            for error in errors:
                _LOGGER.warning("cleanup: %s", error)

    async def __aenter__(self) -> SB20Client:
        await self.connect()
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        await self.close()

    async def _release_transport(self) -> list[str]:
        """Stop every started stream and disconnect, attempting every step."""

        errors: list[str] = []
        transport = self._transport
        if transport is None:
            return errors
        for stream, uuid in list(self._started.items()):
            try:
                await asyncio.wait_for(
                    transport.stop_notify(uuid), timeout=_CLEANUP_TIMEOUT_S
                )
            except Exception as exc:
                errors.append(f"stop {stream.value} notifications: {exc}")
        self._started.clear()
        try:
            if transport.is_connected:
                await asyncio.wait_for(
                    transport.disconnect(), timeout=_CLEANUP_TIMEOUT_S
                )
        except Exception as exc:
            errors.append(f"disconnect: {exc}")
        if self._owns_transport:
            self._transport = None
        return errors

    async def _disconnect_best_effort(self) -> None:
        self._closing = True
        errors: list[str] = []
        try:
            errors = await self._release_transport()
        finally:
            self._telemetry.clear()
            self._started.clear()
            self._status = "not connected"
            self._closing = False
            for error in errors:
                _LOGGER.warning("connect cleanup: %s", error)

    def _on_transport_disconnect(self) -> None:
        """Wake every waiter when the BLE link drops."""

        if self._closed or self._closing or self._disconnect_reason is not None:
            return
        reason = BikeDisconnected("BLE link dropped")
        self._disconnect_reason = reason
        self._status = "disconnected"
        self._note("BLE link dropped")
        pending = self._pending
        if pending is not None and not pending.future.done():
            pending.future.set_exception(reason)
        for waiter in (self._store_waiter, self._gear_store_waiter):
            if waiter is not None and not waiter.future.done():
                waiter.future.set_exception(BikeDisconnected("BLE link dropped"))
        self._started.clear()
        self._telemetry.clear()
        self._queue.put_nowait(None)

    async def _read_identity(self) -> None:
        assert self._transport is not None
        updates: dict[str, str] = {}
        for key, uuid in uuids.DIS_CHARACTERISTICS.items():
            try:
                raw = await self._transport.read_characteristic(uuid)
                updates[key] = raw.decode("utf-8", "replace").strip("\x00").strip()
            except Exception:
                continue
        try:
            raw = await self._transport.read_characteristic(uuids.DEVICE_NAME)
            name = raw.decode("utf-8", "replace").strip("\x00").strip()
            if name:
                updates["name"] = name
        except Exception:
            pass
        if updates:
            self._identity = replace(self._identity, **updates)

    async def _start_notifications(self) -> None:
        assert self._transport is not None
        streams: list[tuple[Stream, str, bool]] = [(Stream.API, uuids.API_RESPONSE, True)]
        if self._subscribe_buttons:
            streams.append((Stream.BUTTONS, uuids.BUTTON_ACTIVE, True))
            streams.append((Stream.BRAKE, uuids.BRAKE_VALUE, True))
        if self._subscribe_ftms:
            streams.append((Stream.FTMS_DATA, uuids.FTMS_INDOOR_BIKE_DATA, True))
            streams.append((Stream.FTMS_STATUS, uuids.FTMS_STATUS, True))
            streams.append((Stream.FTMS_CONTROL, uuids.FTMS_CONTROL_POINT, True))
        if self._subscribe_csc:
            streams.append((Stream.CSC, uuids.CSC_MEASUREMENT, False))

        for stream, uuid, required in streams:
            try:
                await self._transport.start_notify(uuid, self._callback(stream))
            except Exception as exc:
                if required:
                    raise BikeSubscriptionError(
                        f"cannot enable {stream.value} notifications: {exc}"
                    ) from exc
                self._csc_available = False
                self._note(f"{stream.value} notifications unavailable: {exc}")
                continue
            self._started[stream] = uuid
            self._note(f"notify on {stream.value}")

    # ------------------------------------------------------------------ notifications

    def _callback(self, stream: Stream) -> Callable[[bytes], None]:
        def on_data(data: bytes) -> None:
            self._on_notification(stream, data)

        return on_data

    def add_listener(self, listener: Callable[[Notification], None]) -> None:
        self._listeners.append(listener)

    def remove_listener(self, listener: Callable[[Notification], None]) -> None:
        try:
            self._listeners.remove(listener)
        except ValueError:
            pass

    async def notifications(self) -> AsyncGenerator[Notification, None]:
        """Yield notifications until :meth:`close` or a link drop.

        A dropped link raises :class:`BikeDisconnected` so the consumer can
        decide whether to reconnect.
        """

        while True:
            item = await self._queue.get()
            if item is None:
                reason, self._disconnect_reason = self._disconnect_reason, None
                if reason is not None:
                    raise reason
                return
            yield item

    def _on_notification(self, stream: Stream, data: bytes) -> None:
        record = pdu.decode_notification(stream, data)
        note = Notification(stream=stream, raw=data, record=record, t=time.monotonic())
        for listener in list(self._listeners):
            try:
                listener(note)
            except Exception as exc:  # a listener must not break the BLE callback
                self._note(f"listener failed: {exc}")
        self._queue.put_nowait(note)
        self._resolve_pending(note)
        self._resolve_store(note)
        self._resolve_gear_store(note)

    def _resolve_gear_store(self, note: Notification) -> None:
        waiter = self._gear_store_waiter
        if waiter is None or waiter.future.done():
            return
        if not isinstance(note.record, pdu.GearTransferAck):
            return
        if note.record.command not in (
            pdu.GearingTransferCommand.STORE_SUCCESS,
            pdu.GearingTransferCommand.STORE_INVALID_CRC,
            pdu.GearingTransferCommand.STORE_INTERNAL_ERROR,
        ):
            return
        if note.t < waiter.started:
            return
        waiter.future.set_result(note.record)

    def _resolve_store(self, note: Notification) -> None:
        waiter = self._store_waiter
        if waiter is None or waiter.future.done():
            return
        if not isinstance(note.record, StoreSettings):
            return
        if note.t < waiter.started:
            return
        waiter.messages.append(note.record)
        if note.record.status in (
            StoreSettingsCommand.ERROR,
            StoreSettingsCommand.STORED,
            StoreSettingsCommand.STORED_AND_APPLIED,
        ):
            waiter.future.set_result(tuple(waiter.messages))

    def _resolve_pending(self, note: Notification) -> None:
        pending = self._pending
        if pending is None or pending.future.done():
            return
        if not self._matches(pending.request, note):
            return
        record = note.record
        if isinstance(record, pdu.ErrorPdu):
            code = record.error.name if record.error is not None else str(record.code)
            pending.future.set_exception(
                BikeProtocolError(
                    f"{pending.request.name}: bike returned error {code} for "
                    f"operation 0x{record.failed_op:02X}"
                )
            )
            return
        if isinstance(record, pdu.PermissionDenied):
            pending.future.set_exception(
                BikePermissionError("bike reported permission denied (0xFA)")
            )
            return
        if isinstance(record, pdu.AlreadyConnected):
            pending.future.set_exception(
                BikeAlreadyConnectedError(
                    f"bike is controlled by another device: {record.controller_address!r}"
                )
            )
            return
        pending.future.set_result(record)

    def _matches(self, request: ReadRequest | WriteRequest, note: Notification) -> bool:
        record = note.record
        request_op = request.pdu[0]
        if isinstance(record, pdu.ErrorPdu):
            return record.failed_op == request_op
        if isinstance(record, (pdu.PermissionDenied, pdu.AlreadyConnected)):
            return True
        if isinstance(record, pdu.UnknownPdu):
            return False
        if record.op != request_op:
            return False

        if request_op == pdu.Op.POWER_DATA:
            return (
                isinstance(record, pdu.PowerDataAck)
                and int(record.command) == request.pdu[1]
            )
        if request_op == pdu.Op.GEARING:
            return self._matches_gearing(request.pdu, record)
        if request_op == pdu.Op.BUTTONS:
            return (
                isinstance(record, pdu.ButtonAssignments)
                and record.command == request.pdu[1]
            )
        if request_op == pdu.Op.POWER_METER_PAIRING:
            return isinstance(record, pdu.PowerMeterPairing) and record.command != 0
        if request_op == pdu.Op.SENSOR_FIRMWARE:
            if not isinstance(record, pdu.SensorFirmware):
                return False
            request_command = request.pdu[1] if len(request.pdu) > 1 else -1
            return record.command in {
                request_command,
                pdu.FirmwareCommand.NO_VERSION,
                pdu.FirmwareCommand.VERSION,
            }
        if request_op == pdu.Op.BATTERY:
            return isinstance(record, pdu.Battery)
        return True

    @staticmethod
    def _matches_gearing(request: bytes, record: pdu.Pdu) -> bool:
        if len(request) < 2:
            return False
        sub = request[1]
        if isinstance(record, pdu.GearNotification):
            # A GET (0x0C/0/2) is answered by a sub-op 1 notification.
            return sub == pdu.GearingSubOp.SUBSCRIPTION and len(request) >= 3 and request[2] == 2
        if isinstance(record, pdu.GearSubscriptionAck):
            return (
                sub == pdu.GearingSubOp.SUBSCRIPTION
                and len(request) >= 3
                and record.command == request[2]
            )
        if sub != pdu.GearingSubOp.TRANSFER or len(request) < 3:
            return False
        command = request[2]
        if isinstance(record, pdu.GearModelHeader):
            return (
                command == pdu.GearingTransferCommand.GET_CURRENT_MODEL
                and record.command in (
                    pdu.GearingTransferCommand.GET_CURRENT_MODEL_HEADER,
                    pdu.GearingTransferCommand.GET_CURRENT_MODEL_SUCCESS,
                )
            )
        if isinstance(record, pdu.GearTransferAck):
            if command == pdu.GearingTransferCommand.GET_CURRENT_MODEL:
                return False
            if command == pdu.GearingTransferCommand.REQUEST_TRANSFER:
                return record.command in (
                    pdu.GearingTransferCommand.REQUEST_TRANSFER_SUCCESS,
                    pdu.GearingTransferCommand.REQUEST_TRANSFER_ERROR,
                )
            if command == pdu.GearingTransferCommand.APPLY:
                return record.command in (
                    pdu.GearingTransferCommand.APPLY_SUCCESS,
                    pdu.GearingTransferCommand.STORE_INVALID_CRC,
                    pdu.GearingTransferCommand.STORE_INTERNAL_ERROR,
                )
            return False
        return False

    # ------------------------------------------------------------------ requests

    async def request(self, request: ReadRequest) -> pdu.Pdu:
        """Send one allow-listed read request and wait for its response PDU."""

        return await self._exchange(request, configuration=False)

    async def write_request(
        self,
        request: WriteRequest,
        *,
        timeout: float | None = None,
    ) -> pdu.Pdu:
        """Send one explicitly approved configuration write and await its response."""

        return await self._exchange(request, configuration=True, timeout=timeout)

    async def ftms_control(self, value: bytes) -> None:
        """Write one Fitness Machine Control Point value to the bike.

        The write is byte-transparent so every FTMS procedure the bike
        implements stays reachable; :func:`openstage.pdu.ensure_ftms_control`
        only rejects unknown op codes and short parameters. The bike answers
        later with an indication on the same characteristic. The write takes
        the request lock, so it never overlaps another outgoing BLE operation;
        concurrent callers queue on that lock in arrival order.
        """

        pdu.ensure_ftms_control(value)
        transport = self._transport
        if transport is None or not transport.is_connected:
            raise BikeConnectionError("not connected")
        async with self._request_lock:
            try:
                await transport.write_characteristic(
                    uuids.FTMS_CONTROL_POINT, bytes(value)
                )
            except Exception as exc:
                raise BikeConnectionError(f"write failed: {exc}") from exc

    async def _exchange(
        self,
        request: ReadRequest | WriteRequest,
        *,
        configuration: bool,
        timeout: float | None = None,
    ) -> pdu.Pdu:
        if configuration:
            pdu.ensure_write_allowed(request.pdu)
        else:
            pdu.ensure_read_only(request.pdu)
        async with self._request_lock:
            return await self._exchange_locked(
                request, configuration=configuration, timeout=timeout
            )

    async def _exchange_locked(
        self,
        request: ReadRequest | WriteRequest,
        *,
        configuration: bool,
        timeout: float | None = None,
    ) -> pdu.Pdu:
        """Exchange one request while the caller holds ``_request_lock``."""

        if self._pending is not None:
            raise BikeProtocolError("another request is already outstanding")
        loop = asyncio.get_running_loop()
        future: asyncio.Future[pdu.Pdu] = loop.create_future()
        pending = _Pending(request=request, future=future)
        self._pending = pending
        try:
            await self._write_raw(request.pdu, configuration=configuration)
            limit = self._request_timeout if timeout is None else timeout
            return await asyncio.wait_for(future, timeout=limit)
        except TimeoutError as exc:
            limit = self._request_timeout if timeout is None else timeout
            raise BikeTimeout(
                f"{request.name}: no response within {limit:g}s"
            ) from exc
        finally:
            if self._pending is pending:
                self._pending = None

    @staticmethod
    def _gearing_ack(
        record: pdu.Pdu,
        expected: pdu.GearingTransferCommand,
        label: str,
    ) -> pdu.GearTransferAck:
        if not isinstance(record, pdu.GearTransferAck):
            raise BikeProtocolError(
                f"{label}: expected gearing acknowledgement, got "
                f"{type(record).__name__} ({record.raw.hex()})"
            )
        if record.command != expected:
            raise BikeProtocolError(
                f"{label}: bike returned command {record.command} "
                f"({record.raw.hex()})"
            )
        return record

    async def _apply_gearing(
        self,
        plan: GearPlan,
    ) -> tuple[pdu.GearTransferAck, pdu.GearTransferAck]:
        """Send header, fragments and APPLY as one atomic transfer."""

        pdu.ensure_write_allowed(plan.header.pdu)
        pdu.ensure_write_allowed(plan.apply.pdu)
        async with self._request_lock:
            request = await self._exchange_locked(plan.header, configuration=True)
            request_ack = self._gearing_ack(
                request,
                pdu.GearingTransferCommand.REQUEST_TRANSFER_SUCCESS,
                "gearing request",
            )
            for fragment in plan.fragments:
                await self._write_raw(fragment.pdu, configuration=True)
                # Write Without Response returns before the controller has
                # drained the packet.  A short gap keeps the last fragment from
                # racing the APPLY that follows.
                await asyncio.sleep(_GEARING_FRAGMENT_GAP_S)
            # APPLY installs the map in RAM; the active gear changes at this
            # point and no later command is needed.
            applied = await self._exchange_locked(
                plan.apply, configuration=True, timeout=self._gearing_apply_timeout
            )
            apply_ack = self._gearing_ack(
                applied,
                pdu.GearingTransferCommand.APPLY_SUCCESS,
                "gearing apply",
            )
        return request_ack, apply_ack

    async def store_gearing(self) -> GearStoreResult:
        """Write the applied map to flash and re-read the stored header.

        The applied map lives in the bike's RAM buffer, so this can be called
        after APPLY, even from another connection in the same power cycle.
        A ``0C 02 07`` header read clears that buffer; the bike then answers
        ``0C 02 06``. The request lock is held for the whole wait, so no other
        command reaches the bike while the store runs, because a flash read
        during the write can disturb it. ``0A`` means queued. Flash
        housekeeping can make completions progressively slower, so the default
        bound is 240 s. On success the stored header is read back with
        ``0C 02 07``; a failed or unconfirmed store does not read flash.
        """

        request = pdu.build_gearing_store()
        pdu.ensure_write_allowed(request.pdu)
        loop = asyncio.get_running_loop()
        future: asyncio.Future[pdu.GearTransferAck] = loop.create_future()
        waiter = _GearStoreWaiter(started=time.monotonic(), future=future)
        async with self._request_lock:
            if self._pending is not None:
                raise BikeProtocolError("another request is already outstanding")
            self._gear_store_waiter = waiter
            try:
                await self._write_raw(request.pdu, configuration=True)
                try:
                    ack = await asyncio.wait_for(
                        asyncio.shield(future), timeout=self._gearing_store_timeout
                    )
                except TimeoutError:
                    return GearStoreResult(
                        None,
                        f"gearing store: no terminal 0x0C/2/4-6 within "
                        f"{self._gearing_store_timeout:g}s",
                    )
            finally:
                if self._gear_store_waiter is waiter:
                    self._gear_store_waiter = None
        if ack.command != pdu.GearingTransferCommand.STORE_SUCCESS:
            return GearStoreResult(
                ack, f"bike returned command 0x{ack.command:02X}"
            )
        # Terminal frame received; other commands are allowed again.  The
        # active buffer is cleared by this read, but a successful store means
        # flash and RAM are the same map.
        try:
            header_after = await self.get_model_header()
        except BikeError:
            header_after = None
        return GearStoreResult(ack, None, header_after)

    async def _write_raw(self, data: bytes, *, configuration: bool) -> None:
        if configuration:
            pdu.ensure_write_allowed(data)
        else:
            pdu.ensure_read_only(data)
        transport = self._transport
        if transport is None or not transport.is_connected:
            raise BikeConnectionError("not connected")
        try:
            await transport.write_characteristic(uuids.API_REQUEST, data)
        except Exception as exc:
            raise BikeConnectionError(f"write failed: {exc}") from exc

    async def store_settings(self) -> StoreResult:
        """Send ``0xFD/0`` and wait for ``FD 03``/``FD 04``/``FD 02``.

        ``FD 04`` from an earlier Set is ignored; only messages at or after this
        store request are considered.
        """

        request = pdu.STORE_SETTINGS_WRITE
        pdu.ensure_write_allowed(request.pdu)
        async with self._request_lock:
            if self._pending is not None:
                raise BikeProtocolError("another request is already outstanding")
            loop = asyncio.get_running_loop()
            future: asyncio.Future[tuple[StoreSettings, ...]] = loop.create_future()
            waiter = _StoreWaiter(started=time.monotonic(), future=future, messages=[])
            self._store_waiter = waiter
            try:
                await self._write_raw(request.pdu, configuration=True)
                messages = await asyncio.wait_for(future, timeout=self._store_timeout)
                return StoreResult(messages)
            except TimeoutError as exc:
                raise BikeTimeout(
                    f"store_settings: no stored/error response within {self._store_timeout:g}s"
                ) from exc
            finally:
                if self._store_waiter is waiter:
                    self._store_waiter = None

    async def _write_one(
        self,
        request: pdu.WriteRequest,
        expected: type[T],
        label: str,
        *,
        store: bool = False,
    ) -> WriteResult:
        response = self._expect(await self.write_request(request), expected, label)
        if not store:
            return WriteResult(set_response=response)
        completed = (WriteResult(set_response=response),)
        try:
            store_result = await self.store_settings()
        except BikeError as exc:
            raise SettingsCommitError("store settings", completed, str(exc)) from exc
        return WriteResult(set_response=response, store=store_result)

    @staticmethod
    def _expect(record: pdu.Pdu, expected: type[T], label: str) -> T:
        if not isinstance(record, expected):
            raise BikeProtocolError(
                f"{label}: expected {expected.__name__}, got {type(record).__name__} "
                f"({record.raw.hex()})"
            )
        return record

    async def get_scts_identity(self) -> pdu.SctsIdentity:
        return self._expect(await self.request(pdu.SCTS_IDENTITY), pdu.SctsIdentity,
                            "scts_identity")

    async def get_bike_status(self) -> pdu.BikeStatus:
        return self._expect(await self.request(pdu.BIKE_STATUS), pdu.BikeStatus, "bike_status")

    async def get_pairing(self) -> pdu.PowerMeterPairing:
        return self._expect(await self.request(pdu.POWER_METER_PAIRING),
                            pdu.PowerMeterPairing, "power_meter_pairing")

    async def get_parameters(self) -> pdu.Parameters:
        return self._expect(await self.request(pdu.PARAMETERS), pdu.Parameters, "parameters")

    async def get_resistance(self) -> pdu.Resistance:
        return self._expect(await self.request(pdu.RESISTANCE), pdu.Resistance, "resistance")

    async def get_gradient_scaling(self) -> pdu.GradientScaling:
        return self._expect(await self.request(pdu.GRADIENT_SCALING),
                            pdu.GradientScaling, "gradient_scaling")

    async def get_weight(self) -> pdu.Weight:
        return self._expect(await self.request(pdu.WEIGHT), pdu.Weight, "weight")

    async def get_battery(self) -> pdu.Battery:
        return self._expect(await self.request(pdu.BATTERY), pdu.Battery, "battery")

    async def get_buttons(self) -> pdu.ButtonAssignments:
        return self._expect(await self.request(pdu.BUTTONS), pdu.ButtonAssignments, "buttons")

    async def set_weight(
        self,
        user_kg: float,
        equipment_kg: float,
        *,
        store: bool = False,
    ) -> WriteResult:
        """Set both weights; only ``store=True`` makes it survive a power cycle."""

        return await self._write_one(
            pdu.build_set_weight(user_kg, equipment_kg), pdu.Weight, "set_weight",
            store=store,
        )

    async def set_parameters(
        self,
        brake_enabled: bool | None,
        dual_ble_enabled: bool | None,
        *,
        store: bool = False,
    ) -> WriteResult:
        """Set brake/dual-BLE; ``None`` preserves the current field."""

        return await self._write_one(
            pdu.build_set_parameters(brake_enabled, dual_ble_enabled),
            pdu.Parameters, "set_parameters", store=store,
        )

    async def set_gradient_scaling(
        self,
        factor: int,
        delay_enabled: bool = False,
        *,
        store: bool = False,
    ) -> WriteResult:
        """Set the gradient scaling factor (unit 0.5 %) and delay flag."""

        return await self._write_one(
            pdu.build_set_gradient_scaling(factor, delay_enabled),
            pdu.GradientScaling, "set_gradient_scaling", store=store,
        )

    async def set_target_resistance(self, percent: float) -> WriteResult:
        """Set the resistance target as percent 0…100; this selects level mode."""

        return await self._write_one(
            pdu.build_set_resistance_percent(percent), pdu.Resistance,
            "set_target_resistance",
        )

    async def set_crank_length(
        self,
        mm: float,
        *,
        store: bool = False,
    ) -> WriteResult:
        """Set one of the physical crank lengths; this restarts PM pairing."""

        return await self._write_one(
            pdu.build_set_crank_length(mm), pdu.PowerMeterPairing,
            "set_crank_length", store=store,
        )

    async def set_target_power(self, watts: float) -> WriteResult:
        """Set the ERG target; this is a control mode, not a persisted setting."""

        return await self._write_one(
            pdu.build_set_target_power(watts), pdu.Power, "set_target_power",
        )

    async def set_target_grade(self, percent: float) -> WriteResult:
        """Set the simulation grade as percent; this selects simulation mode."""

        return await self._write_one(
            pdu.build_set_grade(percent), pdu.FtmsParameters, "set_target_grade",
        )

    async def set_control_mode(
        self,
        mode: ControlMode,
        target: float = 0.0,
    ) -> WriteResult:
        """Select a control mode by sending its target PDU.

        ``target`` is watts for ERG, percent for resistance, and percent grade
        for simulation. A target of zero still selects the mode.
        """

        if mode is ControlMode.ERG:
            return await self.set_target_power(target)
        if mode is ControlMode.RESISTANCE:
            return await self.set_target_resistance(target)
        if mode is ControlMode.SIMULATION:
            return await self.set_target_grade(target)
        raise ValueError(f"unsupported control mode: {mode!r}")

    async def erg_on(self, watts: float) -> WriteResult:
        """Enable ERG at the requested target power (operation 0x02, command 0)."""

        return await self.set_target_power(watts)

    async def erg_off(self) -> WriteResult:
        """Leave ERG by selecting simulation at 0.000 % grade."""

        return await self.set_target_grade(0.0)

    async def zero_reset(self) -> pdu.ZeroReset:
        """Initiate zero reset and wait for both pedal calibration offsets.

        The two power meters answer independently. A successful reset can first
        return only the left or right offset, so keep listening until both are
        present. A terminal error is returned immediately.
        """

        loop = asyncio.get_running_loop()
        observed: list[pdu.ZeroReset] = []
        complete: asyncio.Future[pdu.ZeroReset] = loop.create_future()

        def listener(note: Notification) -> None:
            record = note.record
            if not isinstance(record, pdu.ZeroReset):
                return
            observed.append(record)
            merged = _merge_zero_reset(observed)
            if record.status is pdu.ZeroResetStatus.SUCCESS:
                if merged.left_offset is not None and merged.right_offset is not None:
                    if not complete.done():
                        complete.set_result(merged)
            elif record.status is not pdu.ZeroResetStatus.INITIATED:
                if not complete.done():
                    complete.set_result(merged)

        deadline = time.monotonic() + self._zero_reset_timeout
        self.add_listener(listener)
        try:
            response = await self.write_request(
                pdu.ZERO_RESET_WRITE, timeout=self._zero_reset_timeout
            )
            first = self._expect(response, pdu.ZeroReset, "zero_reset")
            if first.status is pdu.ZeroResetStatus.INITIATED:
                observed.append(first)
            if complete.done():
                return complete.result()
            remaining = max(0.0, deadline - time.monotonic())
            try:
                return await asyncio.wait_for(complete, timeout=remaining)
            except TimeoutError as exc:
                message = "zero_reset: did not receive both left and right calibration offsets"
                if observed:
                    raise ZeroResetTimeout(message, _merge_zero_reset(observed)) from exc
                raise BikeTimeout(message) from exc
        finally:
            self.remove_listener(listener)

    async def set_buttons(
        self,
        values: Sequence[int],
        *,
        store: bool = False,
    ) -> WriteResult:
        """Set all 15 wire button assignments; only ``store=True`` persists them."""

        return await self._write_one(
            pdu.build_set_buttons(values), pdu.ButtonAssignments, "set_buttons",
            store=store,
        )

    async def set_button_actions(
        self,
        actions: Sequence[pdu.ButtonAction],
        *,
        store: bool = False,
    ) -> WriteResult:
        """Set all 14 physical buttons from semantic actions."""

        return await self._write_one(
            pdu.build_set_button_actions(actions), pdu.ButtonAssignments,
            "set_button_actions", store=store,
        )

    async def set_button_slots(
        self,
        changes: Mapping[int, pdu.ButtonAction],
        *,
        store: bool = False,
    ) -> WriteResult:
        """Change physical buttons 1…14, preserving the others."""

        current = await self.get_buttons()
        return await self._write_one(
            pdu.apply_physical_button_actions(current.values, changes),
            pdu.ButtonAssignments, "set_button_slots", store=store,
        )

    async def get_current_gear(self) -> pdu.GearNotification:
        return self._expect(await self.request(pdu.CURRENT_GEAR), pdu.GearNotification,
                            "current_gear")

    async def get_model_header(self) -> pdu.GearModelHeader:
        return self._expect(await self.request(pdu.GEAR_MODEL), pdu.GearModelHeader,
                            "gear_model")

    async def get_sensor_firmware(
        self, side: Literal["left", "right"]
    ) -> pdu.SensorFirmware:
        request = (
            pdu.SENSOR_FIRMWARE_LEFT if side == "left" else pdu.SENSOR_FIRMWARE_RIGHT
        )
        return self._expect(await self.request(request), pdu.SensorFirmware,
                            f"sensor_firmware_{side}")

    async def query_all(self) -> dict[str, pdu.Pdu | Exception]:
        """Run every read-only query, keeping per-query failures."""

        results: dict[str, pdu.Pdu | Exception] = {}
        for request in pdu.QUERY_REQUESTS:
            try:
                results[request.name] = await self.request(request)
            except BikeError as exc:
                results[request.name] = exc
                self._note(f"query {request.name} failed: {exc}")
        return results

    # ------------------------------------------------------------------ telemetry

    async def subscribe_power_data(self) -> None:
        self._expect(
            await self.request(pdu.SUBSCRIBE_POWER_DATA), pdu.PowerDataAck,
            "subscribe_power_data",
        )
        self._telemetry.add("power")

    async def unsubscribe_power_data(self) -> None:
        self._expect(
            await self.request(pdu.UNSUBSCRIBE_POWER_DATA), pdu.PowerDataAck,
            "unsubscribe_power_data",
        )
        self._telemetry.discard("power")

    async def subscribe_gearing(self) -> None:
        self._expect(
            await self.request(pdu.SUBSCRIBE_GEARING), pdu.GearSubscriptionAck,
            "subscribe_gearing",
        )
        self._telemetry.add("gearing")

    async def unsubscribe_gearing(self) -> None:
        self._expect(
            await self.request(pdu.UNSUBSCRIBE_GEARING), pdu.GearSubscriptionAck,
            "unsubscribe_gearing",
        )
        self._telemetry.discard("gearing")

    async def open_telemetry(self, *, power: bool = True, gearing: bool = True) -> None:
        if power and "power" not in self._telemetry:
            try:
                await self.subscribe_power_data()
            except BikeError as exc:
                self._note(f"power subscription failed: {exc}")
        if gearing and "gearing" not in self._telemetry:
            try:
                await self.subscribe_gearing()
            except BikeError as exc:
                self._note(f"gearing subscription failed: {exc}")

    async def close_telemetry(self) -> None:
        if not self._telemetry:
            return
        if "power" in self._telemetry:
            try:
                await self.unsubscribe_power_data()
            except BikeError:
                pass
        if "gearing" in self._telemetry:
            try:
                await self.unsubscribe_gearing()
            except BikeError:
                pass

    # ------------------------------------------------------------------ misc

    def _note(self, message: str) -> None:
        self._notes.append(message)
        del self._notes[:-200]

    def settings(self) -> BikeSettings:
        """Create a fluent settings builder for this client."""

        return BikeSettings(self)

    def gears(self) -> GearSettings:
        """Create a fluent drivetrain builder for this client."""

        return GearSettings(self)

    def __repr__(self) -> str:
        return f"SB20Client(mode={self._mode!r}, status={self._status!r})"
