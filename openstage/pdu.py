"""Wire format for the Stages SB20 control service and the button/brake service.

This module is pure: no BLE and no I/O. The decoder returns typed records.
Read requests pass :func:`ensure_read_only`; configuration writes pass
:func:`ensure_write_allowed`. The client calls both gates.

Decoders keep the complete characteristic value in ``raw`` and expose the
fields they understand. A frame with an unknown operation or a short payload
becomes :class:`UnknownPdu` rather than an exception, so one unexpected frame
does not stop a session.
"""

from __future__ import annotations

import math
import struct
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import IntEnum, StrEnum
from types import MappingProxyType
from typing import Final, Mapping


class Op(IntEnum):
    """Operation code (octet 0 of every control-service PDU)."""

    SCTS_ID = 0x00
    WIRELESS_SENSOR = 0x01
    POWER = 0x02
    WEIGHT = 0x03
    RESISTANCE = 0x04
    GRADIENT_SCALING = 0x05
    ZERO_RESET = 0x06
    BATTERY = 0x07
    POWER_DATA = 0x08
    ANT_SCANNER = 0x09
    POWER_METER_PAIRING = 0x0A
    BUTTONS = 0x0B
    GEARING = 0x0C
    SENSOR_FIRMWARE = 0x0D
    BIKE_STATUS = 0x0E
    FTMS_PARAMETERS = 0x0F
    PARAMETERS = 0x10
    PERMISSION_DENIED = 0xFA
    ALREADY_CONNECTED = 0xFB
    FACTORY_CONTROL = 0xFC
    STORE_SETTINGS = 0xFD
    ERROR = 0xFE
    LONG_MESSAGE = 0xFF


class PowerDataCommand(IntEnum):
    SUBSCRIBE = 0
    UNSUBSCRIBE = 1
    EVENT = 2


class PairingCommand(IntEnum):
    GET_STATUS = 0
    DEVICES_ARE_PAIRED = 1
    DEVICE_IS_NOT_PAIRED = 2
    RESTART_PAIRING = 3
    IN_PROGRESS = 4
    FAILED = 5


class BatterySource(IntEnum):
    POWER_METER = 0
    HEART_RATE = 1


class GearingSubOp(IntEnum):
    SUBSCRIPTION = 0
    NOTIFICATION = 1
    TRANSFER = 2
    FRAGMENT = 3
    MANUAL_TARGET_POWER = 4


class GearingSubscriptionCommand(IntEnum):
    UNSUBSCRIBE = 0
    SUBSCRIBE = 1
    GET = 2


class GearingTransferCommand(IntEnum):
    REQUEST_TRANSFER = 0
    REQUEST_TRANSFER_SUCCESS = 1
    REQUEST_TRANSFER_ERROR = 2
    STORE = 3
    STORE_SUCCESS = 4
    STORE_INVALID_CRC = 5
    STORE_INTERNAL_ERROR = 6
    GET_CURRENT_MODEL = 7
    GET_CURRENT_MODEL_HEADER = 8
    GET_CURRENT_MODEL_SUCCESS = 9
    STORE_IN_PROGRESS = 10
    APPLY = 11
    APPLY_SUCCESS = 12


class FirmwareCommand(IntEnum):
    SUBSCRIBE = 0
    UNSUBSCRIBE = 1
    GET_LEFT = 2
    NO_VERSION = 3
    GET_RIGHT = 4
    VERSION = 10


class ParametersCommand(IntEnum):
    SET = 0
    GET = 1


class StoreSettingsCommand(IntEnum):
    STORE = 0
    IN_PROGRESS = 1
    ERROR = 2
    STORED = 3
    STORED_AND_APPLIED = 4


class ZeroResetStatus(IntEnum):
    UNRECOGNIZED = -1
    INITIATED = 0
    SUCCESS = 1
    ERROR_NO_CONNECTION = 2
    ERROR_TIMEOUT = 3
    ERROR_REQUEST = 4
    ERROR_INVALID_CRANK_POSITION = 5
    MANUFACTURER_CODE = 6


class ErrorCode(IntEnum):
    INVALID_ARGUMENTS = 0
    INVALID_STATE = 1


class SensorStatus(IntEnum):
    UNRECOGNIZED = -1
    DISCONNECTED = 0
    SEARCHING = 1
    CONNECTED = 2
    CONNECTED_LOW_BATTERY = 3


class PairingStatus(IntEnum):
    UNRECOGNIZED = -1
    UNKNOWN = 0
    PAIRED = 1
    NOT_PAIRED = 2


class ModelType(IntEnum):
    UNRECOGNIZED = -1
    FRONT_REAR_U8 = 0
    FRONT_REAR_U16 = 1
    COEFFICIENTS = 2


class ButtonAction(IntEnum):
    """The five useful user-facing button actions."""

    FRONT_UP = 1
    FRONT_DOWN = 2
    REAR_UP = 3
    REAR_DOWN = 4
    NOT_ASSIGNED = 5


BUTTON_ACTION_ALIASES: Final[Mapping[str, ButtonAction]] = MappingProxyType(
    {
        "front_up": ButtonAction.FRONT_UP,
        "front-up": ButtonAction.FRONT_UP,
        "front+": ButtonAction.FRONT_UP,
        "chainring_plus": ButtonAction.FRONT_UP,
        "chainring+": ButtonAction.FRONT_UP,
        "shift_front_up": ButtonAction.FRONT_UP,
        "front_down": ButtonAction.FRONT_DOWN,
        "front-down": ButtonAction.FRONT_DOWN,
        "front-": ButtonAction.FRONT_DOWN,
        "chainring_minus": ButtonAction.FRONT_DOWN,
        "chainring-": ButtonAction.FRONT_DOWN,
        "shift_front_down": ButtonAction.FRONT_DOWN,
        "rear_up": ButtonAction.REAR_UP,
        "rear-up": ButtonAction.REAR_UP,
        "rear+": ButtonAction.REAR_UP,
        "cassette_plus": ButtonAction.REAR_UP,
        "cassette+": ButtonAction.REAR_UP,
        "shift_rear_up": ButtonAction.REAR_UP,
        "rear_down": ButtonAction.REAR_DOWN,
        "rear-down": ButtonAction.REAR_DOWN,
        "rear-": ButtonAction.REAR_DOWN,
        "cassette_minus": ButtonAction.REAR_DOWN,
        "cassette-": ButtonAction.REAR_DOWN,
        "shift_rear_down": ButtonAction.REAR_DOWN,
        "not_assigned": ButtonAction.NOT_ASSIGNED,
        "not-assigned": ButtonAction.NOT_ASSIGNED,
        "external": ButtonAction.NOT_ASSIGNED,
        "none": ButtonAction.NOT_ASSIGNED,
    }
)


def parse_button_action(value: str) -> ButtonAction:
    key = value.strip().lower().replace(" ", "_")
    try:
        return BUTTON_ACTION_ALIASES[key]
    except KeyError as exc:
        raise ValueError(f"unknown button action: {value!r}") from exc


class Stream(StrEnum):
    API = "api"
    BUTTONS = "buttons"
    BRAKE = "brake"
    FTMS_DATA = "ftms_data"
    FTMS_STATUS = "ftms_status"
    FTMS_CONTROL = "ftms_control"
    CSC = "csc"


# --------------------------------------------------------------------------- records


@dataclass(frozen=True, slots=True)
class Pdu:
    """Base record.  ``raw`` is always the complete characteristic value."""

    raw: bytes
    op: int

    @property
    def operation(self) -> Op | None:
        try:
            return Op(self.op)
        except ValueError:
            return None


@dataclass(frozen=True, slots=True)
class UnknownPdu(Pdu):
    payload: bytes


@dataclass(frozen=True, slots=True)
class SctsIdentity(Pdu):
    identifier: int | None
    version: int | None


@dataclass(frozen=True, slots=True)
class Power(Pdu):
    power_w: float


@dataclass(frozen=True, slots=True)
class Weight(Pdu):
    user_kg: float
    equipment_kg: float


@dataclass(frozen=True, slots=True)
class Resistance(Pdu):
    level: int

    @property
    def percent(self) -> float:
        return self.level / 2.55


@dataclass(frozen=True, slots=True)
class GradientScaling(Pdu):
    factor: int
    delay_enabled: bool

    @property
    def percent(self) -> float:
        return self.factor * 0.5


@dataclass(frozen=True, slots=True)
class ZeroReset(Pdu):
    command: int
    status: ZeroResetStatus
    valid_mask: int
    left_offset: int | None
    right_offset: int | None
    manufacturer_code: int | None
    company_id: int | None
    torque_offset: int | None

    @property
    def successful(self) -> bool:
        return self.status is ZeroResetStatus.SUCCESS


@dataclass(frozen=True, slots=True)
class Battery(Pdu):
    source: BatterySource
    percent: int | None


@dataclass(frozen=True, slots=True)
class PowerData(Pdu):
    mask: int
    instantaneous_w: int | None
    accumulated_w: int | None
    cadence_rpm: int | None
    distribution_pct: int | None
    lr_difference: int | None


@dataclass(frozen=True, slots=True)
class PowerDataAck(Pdu):
    command: PowerDataCommand


@dataclass(frozen=True, slots=True)
class PowerMeterPairing(Pdu):
    command: int
    crank_length_mm: float | None
    left_ant_id: int | None
    left_serial: int | None
    right_ant_id: int | None
    right_serial: int | None


@dataclass(frozen=True, slots=True)
class ButtonAssignments(Pdu):
    command: int
    values: tuple[int, ...]

    @property
    def by_slot(self) -> dict[int, int]:
        return dict(zip(BUTTON_WIRE_SLOTS, self.values))


@dataclass(frozen=True, slots=True)
class GearNotification(Pdu):
    """Current gear.

    For ``FRONT_REAR_U8``/``U16`` the two value fields are teeth.  For
    ``COEFFICIENTS`` the bike puts the selected gear's coefficient in
    ``front_teeth`` and leaves ``rear_teeth`` zero; use :attr:`coefficient`.
    """

    model: ModelType
    front_index: int
    rear_index: int
    front_teeth: int
    rear_teeth: int
    dream_drive: bool
    dream_drive_step: int

    @property
    def coefficient(self) -> int | None:
        if self.model is ModelType.COEFFICIENTS:
            return self.front_teeth
        return None


@dataclass(frozen=True, slots=True)
class GearModelHeader(Pdu):
    command: GearingTransferCommand
    model: ModelType
    front_count: int
    rear_count: int
    crc8: int
    dream_drive: bool
    dream_drive_step: int
    map_size: int


@dataclass(frozen=True, slots=True)
class GearTransferAck(Pdu):
    command: int
    payload: bytes


@dataclass(frozen=True, slots=True)
class GearSubscriptionAck(Pdu):
    command: int


@dataclass(frozen=True, slots=True)
class GearMapFragment(Pdu):
    data: bytes


@dataclass(frozen=True, slots=True)
class GearManualTargetPower(Pdu):
    pass


@dataclass(frozen=True, slots=True)
class SensorFirmware(Pdu):
    command: int
    version: str | None
    ant_id: int | None
    manufacturer: int | None
    major: int | None
    minor: int | None
    patch: int | None


@dataclass(frozen=True, slots=True)
class BikeStatus(Pdu):
    flywheel_rpm: float
    hrm: SensorStatus
    power_meter: SensorStatus
    pairing: PairingStatus


@dataclass(frozen=True, slots=True)
class FtmsParameters(Pdu):
    grade_percent: float


@dataclass(frozen=True, slots=True)
class Parameters(Pdu):
    brake_raw: int
    dual_ble_raw: int

    @property
    def brake_enabled(self) -> bool | None:
        return {0: False, 1: True, 2: None}.get(self.brake_raw)

    @property
    def dual_ble_enabled(self) -> bool | None:
        return {0: None, 1: False, 2: True}.get(self.dual_ble_raw)


@dataclass(frozen=True, slots=True)
class StoreSettings(Pdu):
    command: int

    @property
    def status(self) -> StoreSettingsCommand | None:
        try:
            return StoreSettingsCommand(self.command)
        except ValueError:
            return None

    @property
    def stored(self) -> bool:
        return self.command in (
            StoreSettingsCommand.STORED,
            StoreSettingsCommand.STORED_AND_APPLIED,
        )


@dataclass(frozen=True, slots=True)
class PermissionDenied(Pdu):
    pass


@dataclass(frozen=True, slots=True)
class AlreadyConnected(Pdu):
    controller_address: str


@dataclass(frozen=True, slots=True)
class ErrorPdu(Pdu):
    failed_op: int
    code: int

    @property
    def error(self) -> ErrorCode | None:
        try:
            return ErrorCode(self.code)
        except ValueError:
            return None


@dataclass(frozen=True, slots=True)
class FtmsIndoorBikeData(Pdu):
    flags: int
    speed_kph: float | None = None
    distance_m: int | None = None
    cadence_rpm: float | None = None
    power_w: int | None = None
    average_power_w: int | None = None


@dataclass(frozen=True, slots=True)
class CscMeasurement(Pdu):
    flags: int
    wheel_revs: int | None = None
    wheel_event_time_s: float | None = None
    crank_revs: int | None = None
    crank_event_time_s: float | None = None


@dataclass(frozen=True, slots=True)
class ButtonFrame(Pdu):
    """One Button Active (``0c46be60``) frame.

    Octet 0 selects which event fields follow, and octet 1 is reserved. The
    decoder keeps both and reads the packed 16-bit fields starting at octet 2.
    The first field is the active button mask. Selector values seen from the
    bike include ``0x01`` (press), ``0x08`` (short release) and ``0x09``
    (pair), but the decoder does not depend on that list.
    """

    selector: int
    header: int
    values: tuple[int, ...]

    @property
    def mask(self) -> int | None:
        return self.values[0] if self.values else None


@dataclass(frozen=True, slots=True)
class BrakeFrame(Pdu):
    left: int
    right: int
    extra: tuple[int, ...]


# --------------------------------------------------------------------------- helpers


def _u16(data: bytes, offset: int) -> int | None:
    if offset + 2 > len(data):
        return None
    return int(struct.unpack_from("<H", data, offset)[0])


def _i16(data: bytes, offset: int) -> int | None:
    if offset + 2 > len(data):
        return None
    return int(struct.unpack_from("<h", data, offset)[0])


def _u32(data: bytes, offset: int) -> int | None:
    if offset + 4 > len(data):
        return None
    return int(struct.unpack_from("<I", data, offset)[0])


def _i32(data: bytes, offset: int) -> int | None:
    if offset + 4 > len(data):
        return None
    return int(struct.unpack_from("<i", data, offset)[0])


def _sensor_status(value: int) -> SensorStatus:
    try:
        return SensorStatus(value)
    except ValueError:
        return SensorStatus.UNRECOGNIZED


def _pairing_status(value: int) -> PairingStatus:
    try:
        return PairingStatus(value)
    except ValueError:
        return PairingStatus.UNRECOGNIZED


def _model_type(value: int) -> ModelType:
    try:
        return ModelType(value)
    except ValueError:
        return ModelType.UNRECOGNIZED


def _unknown(data: bytes) -> UnknownPdu:
    return UnknownPdu(raw=data, op=data[0] if data else -1, payload=data[1:])


# --------------------------------------------------------------------------- decoder


def decode_pdu(data: bytes) -> Pdu:
    """Decode one control-service PDU (request, response or event)."""

    if not data:
        return _unknown(data)
    op = data[0]

    if op == Op.SCTS_ID:
        if len(data) >= 5:
            return SctsIdentity(raw=data, op=op, identifier=_u16(data, 1), version=_u16(data, 3))
        return SctsIdentity(raw=data, op=op, identifier=data[1] if len(data) >= 2 else None,
                            version=None)

    if op == Op.POWER:
        power = _i32(data, 2)
        if power is not None:
            return Power(raw=data, op=op, power_w=power * 0.5)
        return _unknown(data)

    if op == Op.WEIGHT:
        user, equipment = _u16(data, 2), _u16(data, 4)
        if user is not None and equipment is not None:
            return Weight(raw=data, op=op, user_kg=user * 0.01,
                          equipment_kg=equipment * 0.01)
        return _unknown(data)

    if op == Op.RESISTANCE:
        if len(data) >= 3:
            return Resistance(raw=data, op=op, level=data[2])
        return _unknown(data)

    if op == Op.GRADIENT_SCALING:
        factor = _u16(data, 2)
        if factor is not None and len(data) >= 5:
            return GradientScaling(raw=data, op=op, factor=factor, delay_enabled=bool(data[4]))
        return _unknown(data)

    if op == Op.ZERO_RESET:
        if len(data) < 3:
            return _unknown(data)
        try:
            status = ZeroResetStatus(data[2])
        except ValueError:
            status = ZeroResetStatus.UNRECOGNIZED
        if len(data) < 4:
            return ZeroReset(
                raw=data,
                op=op,
                command=data[1],
                status=status,
                valid_mask=0,
                left_offset=None,
                right_offset=None,
                manufacturer_code=None,
                company_id=None,
                torque_offset=None,
            )
        mask = data[3]
        offset = 4
        left = right = manufacturer = company = torque = None
        if mask & 0x01:
            left = _i16(data, offset)
            offset += 2
        if mask & 0x02:
            right = _i16(data, offset)
            offset += 2
        if mask & 0x04:
            manufacturer = _u32(data, offset)
            offset += 4
        if mask & 0x08:
            company = _u16(data, offset)
            offset += 2
        if mask & 0x10:
            torque = _i16(data, offset)
        return ZeroReset(
            raw=data,
            op=op,
            command=data[1],
            status=status,
            valid_mask=mask,
            left_offset=left,
            right_offset=right,
            manufacturer_code=manufacturer,
            company_id=company,
            torque_offset=torque,
        )

    if op == Op.BATTERY:
        if len(data) >= 3:
            level = struct.unpack_from("<b", data, 2)[0]
            source = BatterySource.POWER_METER
            if data[1] == BatterySource.HEART_RATE:
                source = BatterySource.HEART_RATE
            return Battery(raw=data, op=op, source=source,
                           percent=level if level >= 0 else None)
        return _unknown(data)

    if op == Op.POWER_DATA:
        command = data[1] if len(data) >= 2 else -1
        if command != PowerDataCommand.EVENT:
            try:
                parsed = PowerDataCommand(command)
            except ValueError:
                return _unknown(data)
            return PowerDataAck(raw=data, op=op, command=parsed)
        if len(data) < 3:
            return _unknown(data)
        mask = data[2]
        return PowerData(
            raw=data,
            op=op,
            mask=mask,
            instantaneous_w=_u32(data, 3) if mask & 0x01 else None,
            accumulated_w=_u32(data, 7) if mask & 0x02 else None,
            cadence_rpm=_i16(data, 11) if mask & 0x04 else None,
            distribution_pct=data[13] if mask & 0x08 and len(data) > 13 else None,
            lr_difference=data[14] if mask & 0x10 and len(data) > 14 else None,
        )

    if op == Op.POWER_METER_PAIRING:
        if len(data) < 2:
            return _unknown(data)
        command = data[1]
        crank = _u16(data, 2)
        return PowerMeterPairing(
            raw=data,
            op=op,
            command=command,
            crank_length_mm=None if crank is None else crank * 0.5,
            left_ant_id=_u16(data, 4),
            left_serial=_u32(data, 6),
            right_ant_id=_u16(data, 10),
            right_serial=_u32(data, 12),
        )

    if op == Op.BUTTONS:
        command = data[1] if len(data) >= 2 else -1
        return ButtonAssignments(raw=data, op=op, command=command,
                                 values=tuple(data[2:2 + len(BUTTON_WIRE_SLOTS)]))

    if op == Op.GEARING:
        return _decode_gearing(data)

    if op == Op.SENSOR_FIRMWARE:
        return _decode_sensor_firmware(data)

    if op == Op.BIKE_STATUS:
        if len(data) < 7:
            return _unknown(data)
        rpm = _u16(data, 2)
        if rpm is None:
            return _unknown(data)
        return BikeStatus(
            raw=data,
            op=op,
            flywheel_rpm=rpm / 4.0,
            hrm=_sensor_status(data[4]),
            power_meter=_sensor_status(data[5]),
            pairing=_pairing_status(data[6]),
        )

    if op == Op.FTMS_PARAMETERS:
        grade = _i32(data, 2)
        if grade is not None:
            return FtmsParameters(raw=data, op=op, grade_percent=grade / 1000.0)
        return _unknown(data)

    if op == Op.PARAMETERS:
        if len(data) >= 4:
            return Parameters(raw=data, op=op, brake_raw=data[2], dual_ble_raw=data[3])
        return _unknown(data)

    if op == Op.STORE_SETTINGS:
        if len(data) >= 2:
            return StoreSettings(raw=data, op=op, command=data[1])
        return _unknown(data)

    if op == Op.PERMISSION_DENIED:
        return PermissionDenied(raw=data, op=op)

    if op == Op.ALREADY_CONNECTED:
        return AlreadyConnected(raw=data, op=op,
                                controller_address=data[1:].decode("utf-8", "replace"))

    if op == Op.ERROR:
        return ErrorPdu(raw=data, op=op,
                        failed_op=data[1] if len(data) >= 2 else -1,
                        code=data[2] if len(data) >= 3 else -1)

    return _unknown(data)


def _decode_gearing(data: bytes) -> Pdu:
    op = Op.GEARING
    if len(data) < 2:
        return _unknown(data)
    try:
        sub = GearingSubOp(data[1])
    except ValueError:
        return UnknownPdu(raw=data, op=op, payload=data[2:])

    if sub is GearingSubOp.SUBSCRIPTION:
        return GearSubscriptionAck(raw=data, op=op,
                                   command=data[2] if len(data) >= 3 else -1)

    if sub is GearingSubOp.NOTIFICATION:
        if len(data) < 11:
            return _unknown(data)
        return GearNotification(
            raw=data,
            op=op,
            model=_model_type(data[2]),
            front_index=data[3],
            rear_index=data[4],
            front_teeth=_u16(data, 5) or 0,
            rear_teeth=_u16(data, 7) or 0,
            dream_drive=bool(data[9]),
            dream_drive_step=data[10],
        )

    if sub is GearingSubOp.TRANSFER:
        if len(data) < 3:
            return _unknown(data)
        command = data[2]
        if command in (GearingTransferCommand.GET_CURRENT_MODEL_HEADER,
                       GearingTransferCommand.GET_CURRENT_MODEL_SUCCESS) and len(data) >= 11:
            return GearModelHeader(
                raw=data,
                op=op,
                command=GearingTransferCommand(command),
                model=_model_type(data[3]),
                front_count=data[4],
                rear_count=data[5],
                crc8=data[6],
                dream_drive=bool(data[7]),
                dream_drive_step=data[8],
                map_size=_u16(data, 9) or 0,
            )
        return GearTransferAck(raw=data, op=op, command=command, payload=data[3:])

    if sub is GearingSubOp.FRAGMENT:
        return GearMapFragment(raw=data, op=op, data=data[2:])

    if sub is GearingSubOp.MANUAL_TARGET_POWER:
        return GearManualTargetPower(raw=data, op=op)

    return UnknownPdu(raw=data, op=op, payload=data[2:])


def _decode_sensor_firmware(data: bytes) -> Pdu:
    op = Op.SENSOR_FIRMWARE
    if len(data) < 2:
        return _unknown(data)
    command = data[1]
    if command == FirmwareCommand.NO_VERSION:
        return SensorFirmware(raw=data, op=op, command=command, version=None, ant_id=None,
                              manufacturer=None, major=None, minor=None, patch=None)
    if command in (FirmwareCommand.GET_LEFT, FirmwareCommand.GET_RIGHT):
        # The response repeats the request command and uses the verbose form:
        # command, uint16 ANT id, UTF-8 version.
        if len(data) >= 4:
            return SensorFirmware(
                raw=data,
                op=op,
                command=command,
                version=data[4:].decode("utf-8", "replace"),
                ant_id=_u16(data, 2),
                manufacturer=None,
                major=None,
                minor=None,
                patch=None,
            )
        return SensorFirmware(raw=data, op=op, command=command, version=None, ant_id=None,
                              manufacturer=None, major=None, minor=None, patch=None)
    if command == FirmwareCommand.VERSION:
        # Binary form is exactly nine octets.  A longer frame is the verbose
        # form: uint16 ANT id followed by UTF-8 text.
        if len(data) == 9:
            return SensorFirmware(
                raw=data,
                op=op,
                command=command,
                version=f"{data[2]}.{data[3]}.{data[4]}",
                ant_id=_u16(data, 7),
                manufacturer=_u16(data, 5),
                major=data[2],
                minor=data[3],
                patch=data[4],
            )
        if len(data) >= 4:
            return SensorFirmware(
                raw=data,
                op=op,
                command=command,
                version=data[4:].decode("utf-8", "replace"),
                ant_id=_u16(data, 2),
                manufacturer=None,
                major=None,
                minor=None,
                patch=None,
            )
        return _unknown(data)
    return SensorFirmware(raw=data, op=op, command=command, version=None, ant_id=None,
                          manufacturer=None, major=None, minor=None, patch=None)


# --------------------------------------------------------------------------- non-API streams


def decode_ftms_indoor_bike_data(data: bytes) -> FtmsIndoorBikeData:
    """Decode the SB20's non-standard ``0x2AD2`` frames.

    The bike does not follow the standard field order. It sends one of three
    short shapes: speed only, distance only, or cadence with power. Any other
    shape keeps its flags and raw bytes instead of being forced through a
    standard parser.
    """

    op = 0x2AD2
    if len(data) < 2:
        return FtmsIndoorBikeData(raw=data, op=op, flags=0)
    flags = int(struct.unpack_from("<H", data, 0)[0])
    if len(data) == 4 and flags == 0x0000:
        return FtmsIndoorBikeData(raw=data, op=op, flags=flags,
                                  speed_kph=(_u16(data, 2) or 0) * 0.01)
    if len(data) == 5 and flags == 0x0011:
        return FtmsIndoorBikeData(raw=data, op=op, flags=flags,
                                  distance_m=data[2] | (data[3] << 8) | (data[4] << 16))
    if len(data) == 8 and flags == 0x00C5:
        return FtmsIndoorBikeData(
            raw=data,
            op=op,
            flags=flags,
            cadence_rpm=(_u16(data, 2) or 0) * 0.5,
            power_w=_u16(data, 4),
            average_power_w=_u16(data, 6),
        )
    return FtmsIndoorBikeData(raw=data, op=op, flags=flags)


def decode_csc(data: bytes) -> CscMeasurement:
    """Decode a Cycling Speed and Cadence measurement (crank data used here)."""

    op = 0x2A5B
    if not data:
        return CscMeasurement(raw=data, op=op, flags=0)
    flags = data[0]
    offset = 1
    wheel_revs: int | None = None
    wheel_time: float | None = None
    crank_revs: int | None = None
    crank_time: float | None = None
    if flags & 0x01 and len(data) >= offset + 6:
        wheel_revs = _u32(data, offset)
        wheel_time = (_u16(data, offset + 4) or 0) / 1024.0
        offset += 6
    if flags & 0x02 and len(data) >= offset + 4:
        crank_revs = _u16(data, offset)
        crank_time = (_u16(data, offset + 2) or 0) / 1024.0
    return CscMeasurement(raw=data, op=op, flags=flags, wheel_revs=wheel_revs,
                          wheel_event_time_s=wheel_time, crank_revs=crank_revs,
                          crank_event_time_s=crank_time)


def decode_button_frame(data: bytes) -> ButtonFrame:
    values: list[int] = []
    for offset in (2, 4, 6, 8):
        value = _u16(data, offset)
        if value is not None:
            values.append(value)
    return ButtonFrame(raw=data, op=0xBE60, selector=data[0] if data else -1,
                       header=data[1] if len(data) > 1 else -1, values=tuple(values))


def decode_brake_frame(data: bytes) -> BrakeFrame:
    left = _u16(data, 0) or 0
    right = _u16(data, 2) or 0
    extra: list[int] = []
    for offset in range(4, len(data) - 1, 2):
        value = _u16(data, offset)
        if value is not None:
            extra.append(value)
    return BrakeFrame(raw=data, op=0xBE61, left=left, right=right, extra=tuple(extra))


def decode_notification(stream: Stream, data: bytes) -> Pdu:
    """Decode a characteristic notification according to the stream it came from."""

    if stream is Stream.API:
        return decode_pdu(data)
    if stream is Stream.BUTTONS:
        return decode_button_frame(data)
    if stream is Stream.BRAKE:
        return decode_brake_frame(data)
    if stream is Stream.FTMS_DATA:
        return decode_ftms_indoor_bike_data(data)
    if stream is Stream.CSC:
        return decode_csc(data)
    return UnknownPdu(raw=data, op=-1, payload=data)


# --------------------------------------------------------------------------- read-only requests


@dataclass(frozen=True, slots=True)
class ReadRequest:
    """A request that is safe to send during the read-only phase."""

    name: str
    pdu: bytes
    label: str

    @property
    def hex(self) -> str:
        return self.pdu.hex()


SCTS_IDENTITY: Final = ReadRequest("scts_identity", b"\x00\x00",
                                   "SCTS identifier handshake (identity query)")
BIKE_STATUS: Final = ReadRequest("bike_status", b"\x0e\x00", "get bike status")
POWER_METER_PAIRING: Final = ReadRequest("power_meter_pairing", b"\x0a\x00",
                                         "get power-meter pairing state")
PARAMETERS: Final = ReadRequest("parameters", b"\x10\x01", "read back parameters")
RESISTANCE: Final = ReadRequest("resistance", b"\x04\x00", "read back resistance level")
GRADIENT_SCALING: Final = ReadRequest("gradient_scaling", b"\x05\x00",
                                      "read back gradient scaling")
WEIGHT: Final = ReadRequest("weight", b"\x03\x00", "read back user/equipment weight")
BATTERY: Final = ReadRequest("battery", b"\x07\x00", "get power-meter battery")
BUTTONS: Final = ReadRequest("buttons", b"\x0b\x01", "read back button configuration")
CURRENT_GEAR: Final = ReadRequest("current_gear", b"\x0c\x00\x02", "get current gear")
GEAR_MODEL: Final = ReadRequest("gear_model", b"\x0c\x02\x07",
                                "get stored drivetrain model header (read-only)")
SENSOR_FIRMWARE_LEFT: Final = ReadRequest("sensor_firmware_left", b"\x0d\x02",
                                          "get left sensor firmware version")
SENSOR_FIRMWARE_RIGHT: Final = ReadRequest("sensor_firmware_right", b"\x0d\x04",
                                           "get right sensor firmware version")

SUBSCRIBE_POWER_DATA: Final = ReadRequest("subscribe_power_data", b"\x08\x00",
                                          "subscribe to power data (reversible)")
UNSUBSCRIBE_POWER_DATA: Final = ReadRequest("unsubscribe_power_data", b"\x08\x01",
                                            "unsubscribe from power data")
SUBSCRIBE_GEARING: Final = ReadRequest("subscribe_gearing", b"\x0c\x00\x01",
                                       "subscribe to gearing (reversible)")
UNSUBSCRIBE_GEARING: Final = ReadRequest("unsubscribe_gearing", b"\x0c\x00\x00",
                                         "unsubscribe from gearing")

#: Queries issued by ``SB20Client.query_all``; responses arrive as notifications.
QUERY_REQUESTS: Final[tuple[ReadRequest, ...]] = (
    BIKE_STATUS,
    POWER_METER_PAIRING,
    PARAMETERS,
    RESISTANCE,
    GRADIENT_SCALING,
    WEIGHT,
    BATTERY,
    BUTTONS,
    CURRENT_GEAR,
    GEAR_MODEL,
    SENSOR_FIRMWARE_LEFT,
    SENSOR_FIRMWARE_RIGHT,
)

_READ_ONLY: Final[tuple[ReadRequest, ...]] = (
    SCTS_IDENTITY,
    *QUERY_REQUESTS,
    SUBSCRIBE_POWER_DATA,
    UNSUBSCRIBE_POWER_DATA,
    SUBSCRIBE_GEARING,
    UNSUBSCRIBE_GEARING,
)

#: Exact-value allow-list.  A configuration write is not representable.
READ_ONLY_REQUESTS: Final[Mapping[bytes, str]] = MappingProxyType(
    {request.pdu: request.label for request in _READ_ONLY}
)

#: Wire order of the 15 button assignments carried by operation ``0x0B``.
BUTTON_WIRE_SLOTS: Final[tuple[int, ...]] = (1, 2, 3, 6, 7, 8, 11, 12, 13, 14, 15, 16, 17, 18, 19)

#: The button-active mask maps bits 0..13 to these slots (slot 19 has no bit).
BUTTON_MASK_SLOTS: Final[tuple[int, ...]] = BUTTON_WIRE_SLOTS[:14]

#: Physical crank lengths accepted by the SB20 configuration.
CRANK_LENGTHS_MM: Final[tuple[float, ...]] = (165.0, 170.0, 172.5, 175.0)
_CRANK_RAW: Final[frozenset[int]] = frozenset(round(mm * 2) for mm in CRANK_LENGTHS_MM)


@dataclass(frozen=True, slots=True)
class WriteRequest:
    """One explicit configuration write, separate from the read-only allow-list."""

    name: str
    pdu: bytes
    label: str

    @property
    def hex(self) -> str:
        return self.pdu.hex()


def _weight_raw(value: float) -> int:
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"weight must be finite and non-negative: {value!r}")
    raw = round(value / 0.01)
    if raw > 0xFFFF:
        raise ValueError(f"weight exceeds 655.35 kg: {value!r}")
    return raw


def _button_value(value: int) -> int:
    if not 0 <= value <= 43:
        raise ValueError(f"button assignment must be 0..43: {value!r}")
    return value


#: Settings saved by ``0xFD/0``; the gearing map uses its own transfer.
STORE_SETTINGS_WRITE: Final = WriteRequest("store_settings", b"\xfd\x00",
                                           "store the settings record in flash")


def build_set_weight(user_kg: float, equipment_kg: float) -> WriteRequest:
    """Build ``0x03/1``; both weights are always sent."""

    pdu = bytes((Op.WEIGHT, 1)) + struct.pack("<HH", _weight_raw(user_kg),
                                              _weight_raw(equipment_kg))
    return WriteRequest("set_weight", pdu,
                        f"set weight: user {user_kg:g} kg, equipment {equipment_kg:g} kg")


def build_set_parameters(
    brake_enabled: bool | None,
    dual_ble_enabled: bool | None,
) -> WriteRequest:
    """Build ``0x10/0``; ``None`` means the field is unchanged."""

    brake = 2 if brake_enabled is None else (1 if brake_enabled else 0)
    dual = 0 if dual_ble_enabled is None else (2 if dual_ble_enabled else 1)
    pdu = bytes((Op.PARAMETERS, 0, brake, dual))
    return WriteRequest("set_parameters", pdu,
                        f"set parameters: brake={brake_enabled}, dual_ble={dual_ble_enabled}")


def build_set_gradient_scaling(factor: int, delay_enabled: bool) -> WriteRequest:
    if not 0 <= factor <= 0xFFFF:
        raise ValueError(f"gradient factor must be 0..65535: {factor!r}")
    pdu = bytes((Op.GRADIENT_SCALING, 1)) + struct.pack("<H", factor) + bytes((int(delay_enabled),))
    return WriteRequest("set_gradient_scaling", pdu,
                        f"set gradient scaling: factor {factor} ({factor * 0.5:g}%), "
                        f"delay={delay_enabled}")


def build_set_gradient_scaling_percent(percent: float, delay_enabled: bool) -> WriteRequest:
    if not 0 <= percent <= 32767.5:
        raise ValueError(f"gradient scaling percent must be 0..32767.5: {percent!r}")
    return build_set_gradient_scaling(round(percent * 2), delay_enabled)


def build_set_resistance(level: int) -> WriteRequest:
    if not 0 <= level <= 0xFF:
        raise ValueError(f"resistance level must be 0..255: {level!r}")
    return WriteRequest("set_resistance", bytes((Op.RESISTANCE, 1, level)),
                        f"set resistance level {level} ({level / 2.55:.1f}%)")


def build_set_resistance_percent(percent: float) -> WriteRequest:
    if not 0 <= percent <= 100:
        raise ValueError(f"resistance percent must be 0..100: {percent!r}")
    return build_set_resistance(round(percent * 255 / 100))


def build_set_crank_length(mm: float) -> WriteRequest:
    if mm not in CRANK_LENGTHS_MM:
        raise ValueError(f"crank length must be one of {CRANK_LENGTHS_MM}: {mm!r}")
    raw = round(mm * 2)
    pdu = bytes((Op.POWER_METER_PAIRING, 3)) + struct.pack("<H", raw)
    return WriteRequest("set_crank_length", pdu,
                        f"set crank length {mm:g} mm (restarts pairing)")


def build_set_target_power(watts: float) -> WriteRequest:
    if not 0 <= watts <= 1000:
        raise ValueError(f"target power must be 0..1000 W: {watts!r}")
    raw = round(watts / 0.5)
    pdu = bytes((Op.POWER, 0)) + struct.pack("<i", raw) + b"\x00" * 4
    return WriteRequest("set_target_power", pdu, f"set ERG target {watts:g} W")


def build_set_grade(percent: float) -> WriteRequest:
    if not -20.0 <= percent <= 20.0:
        raise ValueError(f"grade must be -20..20 %: {percent!r}")
    raw = int(percent * 1000)
    pdu = bytes((Op.FTMS_PARAMETERS, 0)) + struct.pack("<i", raw) + b"\x00" * 4
    return WriteRequest("set_grade", pdu, f"set simulation grade {percent:g} %")


#: Zero reset is an operation, not a persisted setting.
ZERO_RESET_WRITE: Final = WriteRequest("zero_reset", b"\x06\x00",
                                       "initiate zero reset / calibration")


def build_set_buttons(values: Sequence[int]) -> WriteRequest:
    """Build ``0x0B/0`` with exactly 15 wire values."""

    if len(values) != len(BUTTON_WIRE_SLOTS):
        raise ValueError(f"expected {len(BUTTON_WIRE_SLOTS)} button values, got {len(values)}")
    pdu = bytes((Op.BUTTONS, 0)) + bytes(_button_value(value) for value in values)
    return WriteRequest("set_buttons", pdu, "set the 15 button-assignment wire values")


def build_set_button_slots(values: Sequence[int]) -> WriteRequest:
    """Build from the 14 physical SB20 slots; the trailing wire byte is 0."""

    if len(values) != len(BUTTON_MASK_SLOTS):
        raise ValueError(f"expected {len(BUTTON_MASK_SLOTS)} physical slots, got {len(values)}")
    return build_set_buttons((*values, 0))


def build_set_button_actions(actions: Sequence[ButtonAction]) -> WriteRequest:
    """Build from 14 semantic actions in physical-slot order."""

    if len(actions) != len(BUTTON_MASK_SLOTS):
        raise ValueError(f"expected {len(BUTTON_MASK_SLOTS)} actions, got {len(actions)}")
    return build_set_buttons((*(int(action) for action in actions), 0))


#: Dream Drive gear and big-shift limits.
DREAM_DRIVE_GEARS_MIN: Final = 10
DREAM_DRIVE_GEARS_MAX: Final = 50
DREAM_DRIVE_STEP_MIN: Final = 0
DREAM_DRIVE_STEP_MAX: Final = 9

#: Maximum map payload in one ``0C/03`` PDU at MTU 23.
GEARING_FRAGMENT_MAX: Final = 18


def _gear_values(values: Sequence[int], label: str) -> tuple[int, ...]:
    if isinstance(values, (str, bytes)):
        raise ValueError(f"{label} must be a sequence of integers")
    result = tuple(values)
    if not result:
        raise ValueError(f"{label} must not be empty")
    if len(result) > 0xFF:
        raise ValueError(f"{label} has more than 255 entries")
    for value in result:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{label} must contain integers: {value!r}")
        if not 1 <= value <= 0xFF:
            raise ValueError(f"{label} entries must be 1..255: {value!r}")
    if len(set(result)) != len(result):
        raise ValueError(f"{label} must not contain duplicates")
    return result


def crc8(data: bytes) -> int:
    """CRC-8 used by the drivetrain map: poly 0x31, init 0xFF, MSB-first."""

    value = 0xFF
    for byte in data:
        value ^= byte
        for _ in range(8):
            value = ((value << 1) ^ 0x31) & 0xFF if value & 0x80 else (value << 1) & 0xFF
    return value


def sorted_front_gears(values: Sequence[int]) -> tuple[int, ...]:
    """Return front rings in bike order: smallest ring first."""

    return tuple(sorted(_gear_values(values, "front gears")))


def sorted_cassette_gears(values: Sequence[int]) -> tuple[int, ...]:
    """Return cogs in bike order: largest cog first."""

    return tuple(sorted(_gear_values(values, "cassette gears"), reverse=True))


def dream_drive_coefficients(gear_count: int) -> tuple[int, ...]:
    """Return the coefficient table for a Dream Drive gear count.

    Each gear has a virtual ratio ``v = 1 / (0.5 + i * 6.5 / (N - 1))`` and
    the wire coefficient is ``truncate(1000 / v)``. The intermediate ``v``
    must be rounded to float32 first; double precision changes the last
    coefficient and the bike rejects the map. For ``N=24`` the last value is
    ``6999``, not ``7000``.
    """

    if not DREAM_DRIVE_GEARS_MIN <= gear_count <= DREAM_DRIVE_GEARS_MAX:
        raise ValueError(
            f"Dream Drive gears must be {DREAM_DRIVE_GEARS_MIN}.."
            f"{DREAM_DRIVE_GEARS_MAX}: {gear_count!r}"
        )
    coefficients: list[int] = []
    for index in range(gear_count):
        value = 1.0 / (0.5 + index * 6.5 / (gear_count - 1))
        stored = struct.unpack("<f", struct.pack("<f", value))[0]
        coefficient = math.trunc((1.0 / stored) * 1000.0)
        if not 0 <= coefficient <= 0xFFFF:
            raise ValueError(f"Dream Drive coefficient out of range: {coefficient}")
        coefficients.append(coefficient)
    return tuple(coefficients)


def build_dream_drive_map(gear_count: int) -> bytes:
    return b"".join(struct.pack("<H", value) for value in dream_drive_coefficients(gear_count))


def build_preset_index(
    front_sets: Iterable[Sequence[int]],
    cassettes: Iterable[Sequence[int]],
) -> dict[str, list[str]]:
    """Build the pair-fingerprint index used to recover a stored preset.

    Key: ``<front_count>x<rear_count>:<crc8>``; value: deduplicated map
    payloads (hex).  The header CRC covers front teeth followed by cassette
    teeth, so only a pair can be looked up.
    """

    index: dict[str, set[str]] = {}
    cassette_list = tuple(cassettes)
    for front in front_sets:
        front_gears = tuple(sorted(front))
        for cassette in cassette_list:
            cassette_gears = tuple(sorted(cassette, reverse=True))
            payload = bytes(front_gears) + bytes(cassette_gears)
            key = f"{len(front_gears)}x{len(cassette_gears)}:{crc8(payload):02X}"
            index.setdefault(key, set()).add(payload.hex())
    return {key: sorted(values) for key, values in index.items()}


def build_front_rear_map(
    front_gears: Sequence[int],
    cassette_gears: Sequence[int],
) -> bytes:
    """Build a Model Type 0 map: front teeth then cassette teeth."""

    front = sorted_front_gears(front_gears)
    rear = sorted_cassette_gears(cassette_gears)
    return bytes(front) + bytes(rear)


def build_gearing_header(
    *,
    model: ModelType,
    front_count: int,
    rear_count: int,
    crc8: int,
    dream_drive: bool,
    step: int,
    map_size: int,
) -> WriteRequest:
    if model not in (ModelType.FRONT_REAR_U8, ModelType.COEFFICIENTS):
        raise ValueError(f"unsupported drivetrain model: {model!r}")
    if not 1 <= front_count <= 0xFF or not 1 <= rear_count <= 0xFF:
        raise ValueError("sprocket counts must be 1..255")
    if not 0 <= crc8 <= 0xFF or not 0 <= step <= 0xFF:
        raise ValueError("CRC and step must be 0..255")
    if not 0 <= map_size <= 0xFFFF:
        raise ValueError(f"map size out of range: {map_size}")
    if model is ModelType.FRONT_REAR_U8:
        if dream_drive:
            raise ValueError("Model Type 0 is non-Dream-Drive")
        if map_size != front_count + rear_count:
            raise ValueError("Model Type 0 map size must be front + rear")
    else:
        if not dream_drive:
            raise ValueError("Model Type 2 requires Dream Drive")
        if front_count != 1:
            raise ValueError("Model Type 2 requires exactly one front ring")
        if not DREAM_DRIVE_STEP_MIN <= step <= DREAM_DRIVE_STEP_MAX:
            raise ValueError(
                f"Dream Drive step must be {DREAM_DRIVE_STEP_MIN}.."
                f"{DREAM_DRIVE_STEP_MAX}: {step!r}"
            )
        if not DREAM_DRIVE_GEARS_MIN <= rear_count <= DREAM_DRIVE_GEARS_MAX:
            raise ValueError(
                f"Dream Drive gears must be {DREAM_DRIVE_GEARS_MIN}.."
                f"{DREAM_DRIVE_GEARS_MAX}: {rear_count!r}"
            )
        if map_size != rear_count * 2:
            raise ValueError("Model Type 2 map size must be rear count × 2")
    raw = bytes(
        (
            Op.GEARING,
            GearingSubOp.TRANSFER,
            GearingTransferCommand.REQUEST_TRANSFER,
            int(model),
            front_count,
            rear_count,
            crc8,
            int(dream_drive),
            step,
        )
    ) + struct.pack("<H", map_size)
    return WriteRequest("prepare_gearing_transfer", raw,
                        f"prepare drivetrain map: model {int(model)}, "
                        f"{front_count}×{rear_count}, {map_size} B")


def split_gearing_map(map_bytes: bytes) -> tuple[bytes, ...]:
    if not map_bytes:
        raise ValueError("drivetrain map must not be empty")
    return tuple(
        map_bytes[index:index + GEARING_FRAGMENT_MAX]
        for index in range(0, len(map_bytes), GEARING_FRAGMENT_MAX)
    )


def build_gearing_fragment(data: bytes) -> WriteRequest:
    if not 1 <= len(data) <= GEARING_FRAGMENT_MAX:
        raise ValueError(f"fragment must be 1..{GEARING_FRAGMENT_MAX} bytes")
    return WriteRequest(
        "send_gearing_fragment",
        bytes((Op.GEARING, GearingSubOp.FRAGMENT)) + data,
        f"send drivetrain map fragment ({len(data)} B)",
    )


def build_gearing_apply(header: bytes) -> WriteRequest:
    """Echo the transfer header with the APPLY command.

    The SB20 expects the complete transfer header again, not the three-byte
    ``0C 02 0B`` command. Only the command octet changes.
    """

    raw = bytes(header)
    if len(raw) != 11:
        raise ValueError(f"not a gearing transfer header: {raw.hex()}")
    return WriteRequest(
        "apply_gearing_transfer",
        raw[:2] + bytes((GearingTransferCommand.APPLY,)) + raw[3:],
        "apply drivetrain map (RAM only)",
    )


def build_gearing_store() -> WriteRequest:
    return WriteRequest(
        "store_gearing_transfer",
        bytes((Op.GEARING, GearingSubOp.TRANSFER, GearingTransferCommand.STORE)),
        "store drivetrain map in flash",
    )


def _apply_api_button_actions(
    current_wire_values: Sequence[int],
    changes: Mapping[int, ButtonAction],
) -> WriteRequest:
    """Change API wire slots, preserving all other assignments."""

    values = list(current_wire_values[: len(BUTTON_WIRE_SLOTS)])
    values.extend([0] * (len(BUTTON_WIRE_SLOTS) - len(values)))
    index_by_slot = {slot: index for index, slot in enumerate(BUTTON_MASK_SLOTS)}
    for slot, action in changes.items():
        index = index_by_slot.get(slot)
        if index is None:
            raise ValueError(f"slot {slot} is not a physical SB20 button")
        values[index] = int(action)
    values[-1] = 0
    return build_set_buttons(values)


def apply_physical_button_actions(
    current_wire_values: Sequence[int],
    changes: Mapping[int, ButtonAction],
) -> WriteRequest:
    """Change physical buttons 1…14, preserving all other assignments."""

    mapped: dict[int, ButtonAction] = {}
    for physical, action in changes.items():
        if not 1 <= physical <= len(BUTTON_MASK_SLOTS):
            raise ValueError(f"physical button must be 1…{len(BUTTON_MASK_SLOTS)}: {physical}")
        mapped[BUTTON_MASK_SLOTS[physical - 1]] = action
    return _apply_api_button_actions(current_wire_values, mapped)


def button_actions_from_wire(values: Sequence[int]) -> tuple[ButtonAction | None, ...]:
    """Map the 14 physical wire values to semantic actions where possible."""

    actions: list[ButtonAction | None] = []
    for value in values[: len(BUTTON_MASK_SLOTS)]:
        try:
            actions.append(ButtonAction(value))
        except ValueError:
            actions.append(None)
    return tuple(actions)


class SafetyError(RuntimeError):
    """Raised when a caller tries to send an unapproved PDU."""


def ensure_read_only(pdu: bytes) -> str:
    """Return the allow-list description for ``pdu`` or raise :class:`SafetyError`."""

    reason = READ_ONLY_REQUESTS.get(bytes(pdu))
    if reason is None:
        raise SafetyError(
            f"refusing to write {bytes(pdu).hex()!r}: not a read-only query or "
            "reversible telemetry subscription"
        )
    return reason


def ensure_write_allowed(pdu: bytes) -> str:
    """Validate one configuration PDU against the explicit write slice.

    This is deliberately structural, because weight/button/gear values are
    caller-supplied. Factory control, DFU and every operation not listed here
    remain refused.
    """

    raw = bytes(pdu)
    if not raw:
        raise SafetyError("refusing empty configuration write")
    op = raw[0]
    command = raw[1] if len(raw) >= 2 else -1

    if op == Op.WEIGHT and command == 1 and len(raw) == 6:
        return "set user/equipment weight"
    if op == Op.PARAMETERS and command == 0 and len(raw) == 4:
        if raw[2] not in (0, 1, 2) or raw[3] not in (0, 1, 2):
            raise SafetyError(f"invalid parameters tri-state: {raw.hex()}")
        return "set brake/dual-BLE parameters"
    if op == Op.BUTTONS and command == 0 and len(raw) == 2 + len(BUTTON_WIRE_SLOTS):
        if any(value > 43 for value in raw[2:]):
            raise SafetyError(f"invalid button assignment value: {raw.hex()}")
        return "set button assignments"
    if op == Op.POWER and command == 0 and len(raw) in (6, 10):
        power = _i32(raw, 2)
        if power is None or not 0 <= power <= 2000:
            raise SafetyError(f"target power out of range: {raw.hex()}")
        return "set ERG target power"
    if op == Op.FTMS_PARAMETERS and command == 0 and len(raw) in (6, 10):
        grade = _i32(raw, 2)
        if grade is None or not -20000 <= grade <= 20000:
            raise SafetyError(f"grade out of range: {raw.hex()}")
        return "set simulation grade"
    if op == Op.RESISTANCE and command == 1 and len(raw) == 3:
        return "set resistance level"
    if op == Op.GRADIENT_SCALING and command == 1 and len(raw) == 5:
        if raw[4] not in (0, 1):
            raise SafetyError(f"invalid gradient delay flag: {raw.hex()}")
        return "set gradient scaling"
    if op == Op.POWER_METER_PAIRING and command == 3 and len(raw) == 4:
        crank = _u16(raw, 2)
        if crank not in _CRANK_RAW:
            raise SafetyError(f"illegal crank length: {raw.hex()}")
        return "set crank length / restart pairing"
    if op == Op.ZERO_RESET and command == 0 and len(raw) == 2:
        return "initiate zero reset"
    if op == Op.STORE_SETTINGS and command == 0 and len(raw) == 2:
        return "store settings"
    if op == Op.GEARING:
        if command == GearingSubOp.TRANSFER:
            if len(raw) == 11 and raw[2] in (
                GearingTransferCommand.REQUEST_TRANSFER,
                GearingTransferCommand.APPLY,
            ):
                model = raw[3]
                front = raw[4]
                rear = raw[5]
                step = raw[8]
                map_size = int.from_bytes(raw[9:11], "little")
                if model == int(ModelType.FRONT_REAR_U8):
                    if raw[7] != 0:
                        raise SafetyError(f"invalid Model Type 0 header: {raw.hex()}")
                    if not front or not rear or map_size != front + rear:
                        raise SafetyError(f"invalid Model Type 0 size: {raw.hex()}")
                elif model == int(ModelType.COEFFICIENTS):
                    if raw[7] != 1 or front != 1 or map_size != rear * 2:
                        raise SafetyError(f"invalid Model Type 2 header: {raw.hex()}")
                    if not DREAM_DRIVE_GEARS_MIN <= rear <= DREAM_DRIVE_GEARS_MAX:
                        raise SafetyError(f"Dream Drive gear count out of range: {raw.hex()}")
                    if not DREAM_DRIVE_STEP_MIN <= step <= DREAM_DRIVE_STEP_MAX:
                        raise SafetyError(f"Dream Drive step out of range: {raw.hex()}")
                else:
                    raise SafetyError(f"unsupported drivetrain model: {raw.hex()}")
                return (
                    "prepare drivetrain transfer"
                    if raw[2] == GearingTransferCommand.REQUEST_TRANSFER
                    else "apply drivetrain transfer"
                )
            if len(raw) == 3 and raw[2] == GearingTransferCommand.STORE:
                return "store drivetrain transfer"
            raise SafetyError(f"refusing gearing transfer PDU: {raw.hex()}")
        if command == GearingSubOp.FRAGMENT and 3 <= len(raw) <= 2 + GEARING_FRAGMENT_MAX:
            return "send drivetrain map fragment"
        raise SafetyError(f"refusing gearing PDU: {raw.hex()}")

    raise SafetyError(
        f"refusing configuration write {raw.hex()!r}: operation/command/length "
        "is outside the approved write slice"
    )


#: Parameter bytes required by each client-writable Fitness Machine Control
#: Point op code (FTMS Table 4.15). ``0x80`` Response Code is server-to-client
#: only, so it is absent here.
FTMS_CONTROL_PARAMETERS: Final[Mapping[int, int]] = MappingProxyType(
    {
        0x00: 0,  # Request Control
        0x01: 0,  # Reset
        0x02: 2,  # Set Target Speed, UINT16
        0x03: 2,  # Set Target Inclination, SINT16
        0x04: 1,  # Set Target Resistance Level, UINT8
        0x05: 2,  # Set Target Power, SINT16
        0x06: 1,  # Set Target Heart Rate, UINT8
        0x07: 0,  # Start or Resume
        0x08: 1,  # Stop or Pause, control information
        0x09: 2,  # Set Targeted Expended Energy, UINT16
        0x0A: 2,  # Set Targeted Number of Steps, UINT16
        0x0B: 2,  # Set Targeted Number of Strides, UINT16
        0x0C: 3,  # Set Targeted Distance, UINT24
        0x0D: 2,  # Set Targeted Training Time, UINT16
        0x0E: 4,  # Set Targeted Time in Two Heart Rate Zones
        0x0F: 6,  # Set Targeted Time in Three Heart Rate Zones
        0x10: 10,  # Set Targeted Time in Five Heart Rate Zones
        0x11: 6,  # Set Indoor Bike Simulation Parameters
        0x12: 2,  # Set Wheel Circumference, UINT16
        0x13: 1,  # Spin Down Control, control parameter
        0x14: 2,  # Set Targeted Cadence, UINT16
    }
)


def ensure_ftms_control(pdu: bytes) -> str:
    """Validate a Fitness Machine Control Point write and return its label.

    The WFTNP bridge forwards every op code the FTMS specification defines, so
    the check is structural: a known op code with at least its parameter bytes.
    It stops bytes that are not FTMS at all, while leaving the bike to answer
    unsupported procedures with its own result code.
    """

    raw = bytes(pdu)
    if not raw:
        raise SafetyError("refusing empty FTMS control point write")
    op = raw[0]
    expected = FTMS_CONTROL_PARAMETERS.get(op)
    if expected is None:
        raise SafetyError(f"unknown FTMS control point op code: {op:#04x}")
    if len(raw) - 1 < expected:
        raise SafetyError(
            f"FTMS op code {op:#04x} needs {expected} parameter bytes: {raw.hex()}"
        )
    return f"FTMS control point {op:#04x}"
