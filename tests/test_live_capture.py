"""Regression tests for a saved read-only session.

The session used Dream Drive with 24 gears and model type 2 (coefficients).
"""

from __future__ import annotations

import time
from collections import Counter
from pathlib import Path

from openstage import pdu
from openstage.capture import load_capture
from openstage.client import Notification
from openstage.pdu import Stream
from openstage.state import BikeState

FIXTURE = Path(__file__).parent / "fixtures" / "live" / "live-ride.json"


def _apply(state: BikeState) -> None:
    capture = load_capture(FIXTURE)
    state.identity = capture.identity
    state.mtu = capture.mtu
    for frame in capture.frames:
        state.apply(
            Notification(
                stream=frame.stream,
                raw=frame.raw,
                record=pdu.decode_notification(frame.stream, frame.raw),
                t=time.monotonic(),
            )
        )


def test_live_identity_and_readback() -> None:
    state = BikeState()
    _apply(state)

    assert state.identity.name == "Stages Bike 0071"
    assert state.identity.serial == "H0603210071"
    assert state.mtu == 23
    assert state.weight.value is not None
    assert state.weight.value.user_kg == 85.0
    assert state.weight.value.equipment_kg == 7.5
    assert state.battery.value is not None
    assert state.battery.value.percent == 100
    assert state.resistance.value is not None
    assert state.resistance.value.level == 0
    assert state.gradient_scaling.value is not None
    assert state.gradient_scaling.value.percent == 100.0
    assert state.parameters.value is not None
    assert state.parameters.value.brake_enabled is True
    assert state.parameters.value.dual_ble_enabled is True
    assert state.pairing.value is not None
    assert state.pairing.value.crank_length_mm == 165.0


def test_live_firmware_echo_shape() -> None:
    capture = load_capture(FIXTURE)
    firmware = [
        pdu.decode_pdu(frame.raw)
        for frame in capture.frames
        if frame.stream is Stream.API and frame.raw[:1] == b"\x0d"
    ]
    assert len(firmware) == 2
    left, right = firmware
    assert isinstance(left, pdu.SensorFirmware)
    assert isinstance(right, pdu.SensorFirmware)
    assert left.command == pdu.FirmwareCommand.GET_LEFT
    assert left.ant_id == 43392
    assert left.version == "1.8.2"
    assert right.command == pdu.FirmwareCommand.GET_RIGHT
    assert right.ant_id == 45742
    assert right.version == "1.8.2"

    state = BikeState()
    _apply(state)
    assert state.sensor_firmware_left.value is not None
    assert state.sensor_firmware_left.value.version == "1.8.2"
    assert state.sensor_firmware_right.value is not None
    assert state.sensor_firmware_right.value.version == "1.8.2"


def test_live_model_two_is_coefficients_not_teeth() -> None:
    state = BikeState()
    _apply(state)
    view = state.drivetrain_view()
    assert view.model is pdu.ModelType.COEFFICIENTS
    assert (view.front_count, view.rear_count) == (1, 24)
    assert view.map_size == 48
    # The header CRC matches the deterministic Dream Drive map, so state
    # reconstructs all 24 coefficients even though readback is gapped.
    assert view.map_bytes == 48
    assert view.map_complete is True
    assert view.dream_drive is True
    assert view.step == 2
    assert view.teeth_front == {}
    assert view.teeth_rear == {}
    assert view.coefficients[1] == 500
    assert view.coefficients[2] == 782
    assert view.coefficients[4] == 1347
    assert view.coefficients[11] == 3326

    gear = state.gear.value
    assert gear is not None
    assert gear.model is pdu.ModelType.COEFFICIENTS
    assert gear.coefficient == 1347
    assert gear.rear_index == 4


def test_live_brake_sequence() -> None:
    capture = load_capture(FIXTURE)
    brakes: list[tuple[int, int]] = []
    for frame in capture.frames:
        if frame.stream is Stream.BRAKE:
            record = pdu.decode_brake_frame(frame.raw)
            brakes.append((record.left, record.right))
    assert brakes == [(1000, 0), (0, 0), (0, 1000), (0, 0)]


def test_live_button_frames() -> None:
    capture = load_capture(FIXTURE)
    frames = [
        pdu.decode_button_frame(frame.raw)
        for frame in capture.frames
        if frame.stream is Stream.BUTTONS
    ]
    assert len(frames) == 342
    assert {len(frame.raw) for frame in frames} == {4}
    selectors = Counter(frame.selector for frame in frames)
    assert selectors == {0x01: 299, 0x08: 43}
    masks = Counter(frame.mask for frame in frames)
    assert set(masks) == {0x08, 0x10, 0x40, 0x80, 0x100, 0x200}


def test_live_power_masks_and_ranges() -> None:
    capture = load_capture(FIXTURE)
    masks: Counter[int] = Counter()
    powers: list[int] = []
    cadences: list[int] = []
    for frame in capture.frames:
        if frame.stream is not Stream.API or frame.raw[:2] != b"\x08\x02":
            continue
        record = pdu.decode_pdu(frame.raw)
        assert isinstance(record, pdu.PowerData)
        masks[record.mask] += 1
        if record.instantaneous_w is not None:
            powers.append(record.instantaneous_w)
        if record.cadence_rpm is not None:
            cadences.append(record.cadence_rpm)
    assert masks == {0x1F: 84, 0x18: 34}
    assert min(powers) == 0
    assert max(powers) == 230
    assert min(cadences) == 0
    assert max(cadences) == 91


def test_live_ftms_shapes() -> None:
    capture = load_capture(FIXTURE)
    shapes = Counter(
        (frame.raw[:2].hex(), len(frame.raw))
        for frame in capture.frames
        if frame.stream is Stream.FTMS_DATA
    )
    assert shapes == {("0000", 4): 27, ("1100", 5): 28, ("c500", 8): 90}
