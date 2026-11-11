"""UI-independent state folding.

:class:`BikeState` consumes typed :class:`openstage.client.Notification`
objects and exposes the values a dashboard or test wants.  It knows nothing
about Textual, bleak or raw wire bytes.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field, is_dataclass
from enum import IntEnum
from typing import Any, Final, Generic, TypeVar

from . import pdu
from .client import BikeIdentity, Notification
from .pdu import (
    Battery,
    BikeStatus,
    BrakeFrame,
    ButtonAssignments,
    ButtonFrame,
    CscMeasurement,
    ErrorPdu,
    FtmsIndoorBikeData,
    GearMapFragment,
    GearModelHeader,
    GearNotification,
    Parameters,
    PowerData,
    PowerMeterPairing,
    Resistance,
    SctsIdentity,
    SensorFirmware,
    SensorStatus,
    Stream,
    Weight,
)

T = TypeVar("T")

AUTO_SOURCE: Final = "auto"
API_POWER_SOURCE: Final = "API 0x08/2"
FTMS_SOURCE: Final = "FTMS 0x2AD2"
CSC_SOURCE: Final = "CSC 0x2A5B"

#: Preference order for ``auto``.  The Stages API is first wherever it carries
#: the metric; FTMS supplies speed/distance, which the API does not expose.
SOURCE_ORDER: Final[Mapping[str, tuple[str, ...]]] = {
    "power": (API_POWER_SOURCE, FTMS_SOURCE),
    "cadence": (API_POWER_SOURCE, FTMS_SOURCE, CSC_SOURCE),
    "speed": (FTMS_SOURCE,),
    "distance": (FTMS_SOURCE,),
    "balance": (API_POWER_SOURCE,),
    "lr": (API_POWER_SOURCE,),
}

SOURCE_LABELS: Final[Mapping[str, str]] = {
    AUTO_SOURCE: "auto (Stages API first)",
    API_POWER_SOURCE: "Stages API 0x08/2",
    FTMS_SOURCE: "FTMS 0x2AD2",
    CSC_SOURCE: "CSC 0x2A5B",
}


@dataclass(slots=True)
class Reading(Generic[T]):
    """A displayed value plus when and from where it was learned."""

    value: T | None = None
    source: str = ""
    updated_at: float | None = None

    def set(self, value: T | None, source: str, t: float) -> None:
        if value is None:
            return
        self.value = value
        self.source = source
        self.updated_at = t

    @property
    def age(self) -> float | None:
        if self.updated_at is None:
            return None
        return time.monotonic() - self.updated_at


def _r() -> Reading[Any]:
    # Reading is invariant, so default factories are typed as Reading[Any];
    # callers still see the concrete Reading[T] annotation on each field.
    return Reading()


@dataclass(slots=True)
class Metric(Generic[T]):
    """The same quantity as reported by multiple streams."""

    readings: dict[str, Reading[T]] = field(default_factory=dict)

    def set(self, value: T | None, source: str, t: float) -> None:
        if value is None:
            return
        reading = self.readings.get(source)
        if reading is None:
            reading = Reading()
            self.readings[source] = reading
        reading.set(value, source, t)

    def resolve(
        self,
        preference: str,
        order: tuple[str, ...],
        *,
        max_age: float = 2.0,
    ) -> Reading[T]:
        """Resolve the configured source.

        ``auto`` walks ``order`` (API first) and prefers a value no older than
        ``max_age``; if every candidate is stale, the first present value is
        still shown so the display does not blank out between packets.
        """

        if preference != AUTO_SOURCE:
            return self.readings.get(preference, Reading(source=preference))

        fallback: Reading[T] | None = None
        for source in order:
            reading = self.readings.get(source)
            if reading is None or reading.value is None:
                continue
            if fallback is None:
                fallback = reading
            age = reading.age
            if age is None or age <= max_age:
                return reading
        return fallback if fallback is not None else Reading()


def _metric() -> Metric[Any]:
    return Metric()


@dataclass(slots=True)
class SourcePreferences:
    """Per-metric source selection.

    API metrics default to the Stages API.  Speed and distance have no API
    representation, so they default to FTMS; ``auto`` remains selectable and
    resolves the API first where it carries the metric.
    """

    power: str = API_POWER_SOURCE
    cadence: str = API_POWER_SOURCE
    speed: str = FTMS_SOURCE
    distance: str = FTMS_SOURCE
    balance: str = API_POWER_SOURCE
    lr: str = API_POWER_SOURCE


@dataclass(frozen=True, slots=True)
class DrivetrainView:
    model: pdu.ModelType | None
    front_count: int | None
    rear_count: int | None
    crc8: int | None
    step: int | None
    dream_drive: bool | None
    map_size: int | None
    map_bytes: int
    map_complete: bool
    teeth_front: Mapping[int, int]
    teeth_rear: Mapping[int, int]
    coefficients: Mapping[int, int]


@dataclass(slots=True)
class BikeState:
    identity: BikeIdentity = field(default_factory=BikeIdentity)
    mtu: int | None = None
    status: str = ""

    # rider metrics, kept per stream and resolved through SourcePreferences
    power: Metric[int] = field(default_factory=_metric)
    cadence: Metric[float] = field(default_factory=_metric)
    balance: Metric[int] = field(default_factory=_metric)
    lr: Metric[int] = field(default_factory=_metric)
    speed: Metric[float] = field(default_factory=_metric)
    distance: Metric[int] = field(default_factory=_metric)
    preferences: SourcePreferences = field(default_factory=SourcePreferences)

    # bike status
    flywheel_rpm: Reading[float] = field(default_factory=_r)
    hrm: Reading[SensorStatus] = field(default_factory=_r)
    power_meter: Reading[SensorStatus] = field(default_factory=_r)
    pairing_status: Reading[pdu.PairingStatus] = field(default_factory=_r)

    # read-back (queries only)
    resistance: Reading[Resistance] = field(default_factory=_r)
    gradient_scaling: Reading[pdu.GradientScaling] = field(default_factory=_r)
    weight: Reading[Weight] = field(default_factory=_r)
    parameters: Reading[Parameters] = field(default_factory=_r)
    pairing: Reading[PowerMeterPairing] = field(default_factory=_r)
    battery: Reading[Battery] = field(default_factory=_r)
    buttons: Reading[ButtonAssignments] = field(default_factory=_r)
    sensor_firmware_left: Reading[SensorFirmware] = field(default_factory=_r)
    sensor_firmware_right: Reading[SensorFirmware] = field(default_factory=_r)

    # gearing / drivetrain
    gear: Reading[GearNotification] = field(default_factory=_r)
    model_header: GearModelHeader | None = None
    map_bytes: bytes = b""
    teeth_front: dict[int, int] = field(default_factory=dict)
    teeth_rear: dict[int, int] = field(default_factory=dict)
    coefficients: dict[int, int] = field(default_factory=dict)
    _known_drivetrain_t: float | None = field(default=None, repr=False)

    # handlebar inputs
    button_mask: int = 0
    button_events: int = 0
    button_t: float = 0.0
    button_log: deque[tuple[int, int, int]] = field(default_factory=lambda: deque(maxlen=200))
    brake_left: Reading[int] = field(default_factory=_r)
    brake_right: Reading[int] = field(default_factory=_r)

    # miscellaneous
    scts: SctsIdentity | None = None
    last_error: ErrorPdu | None = None
    counts: dict[Stream, int] = field(default_factory=dict)

    _prev_crank: tuple[int, float] | None = field(default=None, repr=False)

    # ------------------------------------------------------------------ resolved metric views

    @property
    def power_w(self) -> Reading[int]:
        return self.power.resolve(self.preferences.power, SOURCE_ORDER["power"])

    @property
    def cadence_rpm(self) -> Reading[float]:
        return self.cadence.resolve(self.preferences.cadence, SOURCE_ORDER["cadence"])

    @property
    def balance_pct(self) -> Reading[int]:
        return self.balance.resolve(self.preferences.balance, SOURCE_ORDER["balance"])

    @property
    def lr_difference(self) -> Reading[int]:
        return self.lr.resolve(self.preferences.lr, SOURCE_ORDER["lr"])

    @property
    def speed_kph(self) -> Reading[float]:
        return self.speed.resolve(self.preferences.speed, SOURCE_ORDER["speed"])

    @property
    def distance_m(self) -> Reading[int]:
        return self.distance.resolve(self.preferences.distance, SOURCE_ORDER["distance"])

    def metric_sources(self, name: str) -> tuple[str, ...]:
        metric = getattr(self, name)
        if not isinstance(metric, Metric):
            raise KeyError(name)
        return tuple(metric.readings)

    # ------------------------------------------------------------------ ingest

    def apply(self, note: Notification) -> str:
        """Fold one notification and return a short description for the event log."""

        self.counts[note.stream] = self.counts.get(note.stream, 0) + 1
        description = self.ingest(note.record, note.stream, note.t)
        return f"{note.hex} -> {description}"

    def ingest(self, record: pdu.Pdu, stream: Stream, t: float | None = None) -> str:
        now = time.monotonic() if t is None else t
        source = stream.value

        if isinstance(record, PowerData):
            if record.instantaneous_w is not None:
                self.power.set(record.instantaneous_w, API_POWER_SOURCE, now)
            if record.cadence_rpm is not None:
                self.cadence.set(float(record.cadence_rpm), API_POWER_SOURCE, now)
            if record.distribution_pct is not None:
                self.balance.set(record.distribution_pct, API_POWER_SOURCE, now)
            if record.lr_difference is not None:
                self.lr.set(record.lr_difference, API_POWER_SOURCE, now)
            return "power_data"

        if isinstance(record, GearNotification):
            self.gear.set(record, source, now)
            if record.model is pdu.ModelType.COEFFICIENTS:
                coefficient = record.coefficient
                if coefficient is not None:
                    self.coefficients[record.rear_index] = coefficient
            else:
                self.teeth_front[record.front_index] = record.front_teeth
                self.teeth_rear[record.rear_index] = record.rear_teeth
            return "gear"

        if isinstance(record, GearModelHeader):
            # A header received before a map we just applied is stale; do not
            # let it clear the map we know is on the bike now.
            if (
                self._known_drivetrain_t is not None
                and now < self._known_drivetrain_t
                and self.model_header is not None
                and _header_key(self.model_header) != _header_key(record)
            ):
                return "model_header (stale)"
            # Success/header responses differ in command and raw bytes but
            # describe the same stored model; compare the model fields only.
            changed = self.model_header is None or _header_key(self.model_header) != _header_key(record)
            self.model_header = record
            if changed:
                self.map_bytes = b""
                self.teeth_front.clear()
                self.teeth_rear.clear()
                self.coefficients.clear()
            if (
                not self.map_bytes
                and record.model is pdu.ModelType.COEFFICIENTS
                and record.dream_drive
                and pdu.DREAM_DRIVE_GEARS_MIN
                <= record.rear_count
                <= pdu.DREAM_DRIVE_GEARS_MAX
            ):
                candidate = pdu.build_dream_drive_map(record.rear_count)
                if (
                    len(candidate) == record.map_size
                    and pdu.crc8(candidate) == record.crc8
                ):
                    self.map_bytes = candidate
            self._parse_map_if_complete()
            return "model_header"

        if isinstance(record, GearMapFragment):
            if (
                self.model_header is not None
                and len(self.map_bytes) >= self.model_header.map_size
            ):
                return "map_fragment (ignored; map already complete)"
            self.map_bytes += record.data
            self._parse_map_if_complete()
            return f"map_fragment ({len(self.map_bytes)} B)"

        if isinstance(record, BikeStatus):
            self.flywheel_rpm.set(record.flywheel_rpm, source, now)
            self.hrm.set(record.hrm, source, now)
            self.power_meter.set(record.power_meter, source, now)
            self.pairing_status.set(record.pairing, source, now)
            return "bike_status"

        if isinstance(record, PowerMeterPairing):
            self.pairing.set(record, source, now)
            return "pairing"

        if isinstance(record, Parameters):
            self.parameters.set(record, source, now)
            return "parameters"

        if isinstance(record, Resistance):
            self.resistance.set(record, source, now)
            return "resistance"

        if isinstance(record, pdu.GradientScaling):
            self.gradient_scaling.set(record, source, now)
            return "gradient_scaling"

        if isinstance(record, Weight):
            self.weight.set(record, source, now)
            return "weight"

        if isinstance(record, Battery):
            self.battery.set(record, source, now)
            return "battery"

        if isinstance(record, ButtonAssignments):
            self.buttons.set(record, source, now)
            return "buttons"

        if isinstance(record, SensorFirmware):
            # The response does not carry the side; the request that produced it does.
            # The dashboard calls ingest_sensor_firmware(side, record) for typed sides.
            if record.command in (pdu.FirmwareCommand.GET_LEFT,):
                self.sensor_firmware_left.set(record, source, now)
            elif record.command in (pdu.FirmwareCommand.GET_RIGHT,):
                self.sensor_firmware_right.set(record, source, now)
            return "sensor_firmware"

        if isinstance(record, SctsIdentity):
            self.scts = record
            return "scts_identity"

        if isinstance(record, ErrorPdu):
            self.last_error = record
            return f"error (op 0x{record.failed_op:02X}, code {record.code})"

        if isinstance(record, FtmsIndoorBikeData):
            if record.speed_kph is not None:
                self.speed.set(record.speed_kph, FTMS_SOURCE, now)
            if record.distance_m is not None:
                self.distance.set(record.distance_m, FTMS_SOURCE, now)
            if record.power_w is not None:
                self.power.set(record.power_w, FTMS_SOURCE, now)
            if record.cadence_rpm is not None:
                self.cadence.set(record.cadence_rpm, FTMS_SOURCE, now)
            return "ftms_indoor_bike_data"

        if isinstance(record, CscMeasurement):
            self._apply_csc(record, now)
            return "csc"

        if isinstance(record, ButtonFrame):
            self._apply_button(record, now)
            return f"button (selector 0x{record.selector:02X}, mask 0x{(record.mask or 0):04X})"

        if isinstance(record, BrakeFrame):
            self.brake_left.set(record.left, source, now)
            self.brake_right.set(record.right, source, now)
            return f"brake (left {record.left}, right {record.right})"

        return type(record).__name__

    def ingest_sensor_firmware(self, side: str, record: SensorFirmware,
                               t: float | None = None) -> None:
        now = time.monotonic() if t is None else t
        if side == "left":
            self.sensor_firmware_left.set(record, "API 0x0D/2", now)
        else:
            self.sensor_firmware_right.set(record, "API 0x0D/4", now)

    # ------------------------------------------------------------------ derived views

    def pressed_mask(self, stale_after: float = 0.4) -> int:
        if self.button_t == 0.0 or time.monotonic() - self.button_t > stale_after:
            return 0
        return self.button_mask

    def set_known_drivetrain(self, header: GearModelHeader, map_bytes: bytes) -> None:
        """Install a map we just applied; readback cannot provide it."""

        self.model_header = header
        self.map_bytes = bytes(map_bytes[: header.map_size])
        self.teeth_front.clear()
        self.teeth_rear.clear()
        self.coefficients.clear()
        self._known_drivetrain_t = time.monotonic()
        self._parse_map_if_complete()

    def drivetrain_view(self) -> DrivetrainView:
        header = self.model_header
        size = header.map_size if header is not None else None
        map_bytes = len(self.map_bytes)
        return DrivetrainView(
            model=header.model if header is not None else None,
            front_count=header.front_count if header is not None else None,
            rear_count=header.rear_count if header is not None else None,
            crc8=header.crc8 if header is not None else None,
            step=header.dream_drive_step if header is not None else None,
            dream_drive=header.dream_drive if header is not None else None,
            map_size=size,
            map_bytes=map_bytes,
            map_complete=size is not None and map_bytes >= size,
            teeth_front=dict(self.teeth_front),
            teeth_rear=dict(self.teeth_rear),
            coefficients=dict(self.coefficients),
        )

    def to_dict(self) -> dict[str, object]:
        """JSON-friendly snapshot for the probe and TUI ``--json`` mode."""

        def reading(value: Reading[Any]) -> dict[str, object]:
            return {
                "value": _jsonable(value.value),
                "source": value.source,
                "age_s": None if value.age is None else round(value.age, 3),
            }

        return {
            "identity": self.identity.as_dict(),
            "mtu": self.mtu,
            "status": self.status,
            "power_w": reading(self.power_w),
            "cadence_rpm": reading(self.cadence_rpm),
            "balance_pct": reading(self.balance_pct),
            "lr_difference": reading(self.lr_difference),
            "speed_kph": reading(self.speed_kph),
            "distance_m": reading(self.distance_m),
            "preferences": asdict(self.preferences),
            "source_values": {
                "power": {source: reading(item) for source, item in self.power.readings.items()},
                "cadence": {source: reading(item) for source, item in self.cadence.readings.items()},
                "speed": {source: reading(item) for source, item in self.speed.readings.items()},
                "distance": {source: reading(item) for source, item in self.distance.readings.items()},
                "balance": {source: reading(item) for source, item in self.balance.readings.items()},
                "lr": {source: reading(item) for source, item in self.lr.readings.items()},
            },
            "flywheel_rpm": reading(self.flywheel_rpm),
            "hrm": reading(self.hrm),
            "power_meter": reading(self.power_meter),
            "pairing_status": reading(self.pairing_status),
            "resistance": reading(self.resistance),
            "gradient_scaling": reading(self.gradient_scaling),
            "weight": reading(self.weight),
            "parameters": reading(self.parameters),
            "pairing": reading(self.pairing),
            "battery": reading(self.battery),
            "buttons": reading(self.buttons),
            "sensor_firmware_left": reading(self.sensor_firmware_left),
            "sensor_firmware_right": reading(self.sensor_firmware_right),
            "gear": reading(self.gear),
            "drivetrain": _jsonable(self.drivetrain_view()),
            "button_mask": self.button_mask,
            "button_events": self.button_events,
            "button_log": list(self.button_log),
            "brake_left": reading(self.brake_left),
            "brake_right": reading(self.brake_right),
            "last_error": _jsonable(self.last_error),
            "counts": {stream.value: count for stream, count in self.counts.items()},
        }

    # ------------------------------------------------------------------ internals

    def _apply_csc(self, record: CscMeasurement, t: float) -> None:
        if record.crank_revs is None or record.crank_event_time_s is None:
            return
        previous = self._prev_crank
        if previous is not None:
            prev_revs, prev_time = previous
            revolutions = (record.crank_revs - prev_revs) & 0xFFFF
            elapsed = (record.crank_event_time_s - prev_time) % 64.0
            if revolutions and elapsed > 0:
                cadence = revolutions / elapsed * 60.0
                if 10.0 <= cadence <= 200.0:
                    self.cadence.set(round(cadence, 1), CSC_SOURCE, t)
        self._prev_crank = (record.crank_revs, record.crank_event_time_s)

    def _apply_button(self, record: ButtonFrame, t: float) -> None:
        mask = record.mask or 0
        if record.selector == 0x01:
            self.button_mask = mask
        elif record.selector == 0x08:
            self.button_mask &= ~mask
        elif record.selector == 0x09:
            other = record.values[1] if len(record.values) > 1 else 0
            self.button_mask = mask | other
        elif record.selector in (0x03, 0x05):
            other = record.values[1] if len(record.values) > 1 else 0
            self.button_mask = other
        elif record.selector == 0x04:
            self.button_mask = 0
        self.button_t = t
        self.button_events += 1
        self.button_log.append((record.selector, mask,
                                record.values[1] if len(record.values) > 1 else 0))

    def _parse_map_if_complete(self) -> None:
        header = self.model_header
        if header is None or len(self.map_bytes) < header.map_size:
            return
        data = self.map_bytes[: header.map_size]
        if header.model is pdu.ModelType.FRONT_REAR_U8:
            front = data[: header.front_count]
            rear = data[header.front_count:header.front_count + header.rear_count]
            self.teeth_front = {i + 1: teeth for i, teeth in enumerate(front)}
            self.teeth_rear = {i + 1: teeth for i, teeth in enumerate(rear)}
        elif header.model is pdu.ModelType.FRONT_REAR_U16:
            width = 2
            front_bytes = data[: header.front_count * width]
            rear_bytes = data[header.front_count * width:
                              (header.front_count + header.rear_count) * width]
            self.teeth_front = {
                i + 1: int.from_bytes(front_bytes[i * width:(i + 1) * width], "little")
                for i in range(header.front_count)
            }
            self.teeth_rear = {
                i + 1: int.from_bytes(rear_bytes[i * width:(i + 1) * width], "little")
                for i in range(header.rear_count)
            }
        elif header.model is pdu.ModelType.COEFFICIENTS:
            self.coefficients = {
                i + 1: int.from_bytes(data[i * 2:(i + 1) * 2], "little")
                for i in range(header.rear_count)
            }


def _header_key(header: GearModelHeader) -> tuple[object, ...]:
    return (
        header.model,
        header.front_count,
        header.rear_count,
        header.crc8,
        header.dream_drive,
        header.dream_drive_step,
        header.map_size,
    )


def _jsonable(value: object) -> object:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, IntEnum):
        return value.name
    if isinstance(value, bytes):
        return value.hex()
    if is_dataclass(value) and not isinstance(value, type):
        return {key: _jsonable(item) for key, item in asdict(value).items()}
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset, deque)):
        return [_jsonable(item) for item in value]
    return str(value)
