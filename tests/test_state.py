"""State folding tests driven by saved captures."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from openstage import pdu
from openstage.capture import load_capture
from openstage.client import Notification
from openstage.pdu import Stream
from openstage.state import BikeState
from openstage.tui.panels import buttons_panel, drivetrain_panel, live_buttons_panel

FIXTURES = Path(__file__).parent / "fixtures" / "legacy"


def note(stream: Stream, hex_frame: str) -> Notification:
    raw = bytes.fromhex(hex_frame)
    return Notification(stream=stream, raw=raw,
                        record=pdu.decode_notification(stream, raw), t=time.monotonic())


def apply_capture(state: BikeState, name: str) -> None:
    capture = load_capture(FIXTURES / name)
    state.identity = capture.identity
    state.mtu = capture.mtu
    for exchange in capture.exchanges:
        for raw in exchange.responses:
            state.apply(Notification(stream=Stream.API, raw=raw,
                                     record=pdu.decode_pdu(raw), t=time.monotonic()))
    for frame in capture.frames:
        state.apply(Notification(stream=frame.stream, raw=frame.raw,
                                 record=pdu.decode_notification(frame.stream, frame.raw),
                                 t=time.monotonic()))


def test_queries_capture_folds_typed_readback() -> None:
    state = BikeState()
    apply_capture(state, "queries.json")

    assert state.identity.serial == "H0603210071"
    assert state.mtu == 23
    assert state.resistance.value is not None
    assert state.resistance.value.level == 0
    assert state.gradient_scaling.value is not None
    assert state.gradient_scaling.value.percent == 100.0
    assert state.weight.value is not None
    assert state.weight.value.user_kg == 84.0
    assert state.weight.value.equipment_kg == 16.0
    assert state.parameters.value is not None
    assert state.parameters.value.brake_enabled is True
    assert state.parameters.value.dual_ble_enabled is True
    assert state.pairing.value is not None
    assert state.pairing.value.crank_length_mm == 165.0
    assert state.gear.value is not None
    assert (state.gear.value.front_teeth, state.gear.value.rear_teeth) == (34, 28)
    assert state.buttons.value is not None
    assert state.buttons.value.by_slot[1] == 3
    view = state.drivetrain_view()
    assert view.front_count == 1
    assert view.rear_count == 12
    assert view.map_size == 13
    assert view.map_bytes == 8  # the bike emitted only one partial map fragment
    assert view.map_complete is False
    assert view.teeth_rear == {}


def test_buttons_capture_folds_events() -> None:
    state = BikeState()
    apply_capture(state, "buttons.json")
    assert state.counts[Stream.BUTTONS] == 59
    assert state.button_events == 59
    assert len(state.button_log) == 59
    assert state.button_log[-1][0] in (0x01, 0x03, 0x04, 0x05, 0x08, 0x09)


def test_ride_capture_folds_telemetry() -> None:
    state = BikeState()
    apply_capture(state, "ride.json")

    assert state.counts[Stream.API] > 200
    assert state.counts[Stream.FTMS_DATA] > 100
    assert state.counts[Stream.CSC] > 50
    assert state.power_w.value is not None
    assert 30 <= state.power_w.value <= 200
    assert state.cadence_rpm.value is not None
    assert 50 <= state.cadence_rpm.value <= 100
    assert state.gear.value is not None
    assert state.gear.value.front_teeth == 34
    assert len(state.teeth_rear) >= 4
    assert state.speed_kph.value is not None
    assert 0 <= state.speed_kph.value <= 45
    assert state.distance_m.value is not None
    assert state.distance_m.value > 0


def test_metric_sources_default_to_api_then_allow_override() -> None:
    state = BikeState()
    now = time.monotonic()
    state.power.set(100, "API 0x08/2", now)
    state.power.set(200, "FTMS 0x2AD2", now)

    assert state.preferences.power == "API 0x08/2"
    assert state.power_w.value == 100
    assert state.power_w.source == "API 0x08/2"

    state.preferences.power = "FTMS 0x2AD2"
    assert state.power_w.value == 200
    assert state.power_w.source == "FTMS 0x2AD2"

    state.preferences.power = "API 0x08/2"
    assert state.power_w.value == 100


def test_cadence_auto_prefers_api_then_csc() -> None:
    state = BikeState()
    now = time.monotonic()
    state.cadence.set(80.0, "API 0x08/2", now)
    state.cadence.set(90.0, "CSC 0x2A5B", now)
    assert state.cadence_rpm.value == 80.0

    state.preferences.cadence = "CSC 0x2A5B"
    assert state.cadence_rpm.value == 90.0
    assert state.cadence_rpm.source == "CSC 0x2A5B"


def test_auto_falls_back_when_preferred_source_is_stale() -> None:
    state = BikeState()
    state.preferences.power = "auto"
    old = time.monotonic() - 10.0
    state.power.set(100, "API 0x08/2", old)
    state.power.set(200, "FTMS 0x2AD2", time.monotonic())
    assert state.power_w.value == 200
    assert state.power_w.source == "FTMS 0x2AD2"


def test_speed_and_distance_are_ftms_only() -> None:
    state = BikeState()
    now = time.monotonic()
    state.speed.set(17.5, "FTMS 0x2AD2", now)
    state.distance.set(1000, "FTMS 0x2AD2", now)
    assert state.speed_kph.value == 17.5
    assert state.distance_m.value == 1000
    assert state.metric_sources("speed") == ("FTMS 0x2AD2",)


def test_physical_button_slots_are_fourteen() -> None:
    assert pdu.BUTTON_MASK_SLOTS == (1, 2, 3, 6, 7, 8, 11, 12, 13, 14, 15, 16, 17, 18)
    assert len(pdu.BUTTON_MASK_SLOTS) == 14
    assert 19 not in pdu.BUTTON_MASK_SLOTS
    assert 19 in pdu.BUTTON_WIRE_SLOTS


def test_live_button_panel_shows_fourteen_slots_in_two_rows() -> None:
    state = BikeState()
    state.apply(note(Stream.BUTTONS, "01000800"))
    text = live_buttons_panel(state).plain
    assert text.count("●") == 1
    assert text.count("○") == 13
    assert "14" in text
    assert "15" not in text
    lines = text.splitlines()
    assert len(lines) == 3
    assert lines[2].startswith("1 events   mask 0x0008")
    assert "configured" not in text


def test_live_button_panel_uses_available_width() -> None:
    state = BikeState()
    state.apply(note(Stream.BUTTONS, "01000800"))
    wide = live_buttons_panel(state, width=156).plain
    assert len(wide.splitlines()) == 2  # one button row + status
    narrow = live_buttons_panel(state, width=80).plain
    assert len(narrow.splitlines()) == 3  # two button rows + status


def test_live_button_panel_without_frames() -> None:
    state = BikeState()
    text = live_buttons_panel(state).plain
    assert len(text.splitlines()) == 3
    assert text.splitlines()[2] == "no button frames received"


def test_assignment_panel_lists_fourteen_physical_slots() -> None:
    state = BikeState()
    state.apply(note(Stream.API, "0b01030405030405030404030102030400"))
    text = buttons_panel(state).plain
    assert "14:" in text
    assert "15:" not in text
    assert "wire slots 1,2,3,6,7,8,11…18" in text


def test_unknown_frames_do_not_raise() -> None:
    state = BikeState()
    state.apply(Notification(stream=Stream.API, raw=b"\x11",
                             record=pdu.decode_pdu(b"\x11"), t=time.monotonic()))
    state.apply(note(Stream.FTMS_DATA, "deadbeef"))
    assert state.counts[Stream.API] == 1
    assert state.counts[Stream.FTMS_DATA] == 1


def test_error_is_retained() -> None:
    state = BikeState()
    state.apply(note(Stream.API, "fe0e01"))
    assert state.last_error is not None
    assert state.last_error.failed_op == 0x0E
    assert state.last_error.code == 1


@pytest.mark.parametrize(
    "raw",
    [
        "0300d0204006",
        "0e000000000201",
        "0a014a0180a91e850a03aeb2c35b6d02",
        "10010102",
        "040000",
        "0500c80000",
        "070050",
        "0b01030405030405030404030102030400",
        "0c0100010322001c000004",
        "0c020800010c0f00040d00",
        "0c032224201c18151311",
    ],
)
def test_hardware_frames_decode_to_known_records(raw: str) -> None:
    record = pdu.decode_pdu(bytes.fromhex(raw))
    assert not isinstance(record, pdu.UnknownPdu)
    assert record.op == bytes.fromhex(raw)[0]


def test_known_drivetrain_ignores_stale_header_and_clears_on_new() -> None:
    state = BikeState()
    map_bytes = pdu.build_dream_drive_map(24)
    header = pdu.GearModelHeader(
        raw=b"",
        op=pdu.Op.GEARING,
        command=pdu.GearingTransferCommand.GET_CURRENT_MODEL_SUCCESS,
        model=pdu.ModelType.COEFFICIENTS,
        front_count=1,
        rear_count=24,
        crc8=pdu.crc8(map_bytes),
        dream_drive=True,
        dream_drive_step=2,
        map_size=len(map_bytes),
    )
    state.set_known_drivetrain(header, map_bytes)
    assert state.drivetrain_view().map_complete is True

    old = pdu.decode_pdu(bytes.fromhex("0c020800010c0f00040d00"))
    assert isinstance(old, pdu.GearModelHeader)
    state.ingest(old, Stream.API, time.monotonic() - 10)
    assert state.model_header is header
    assert state.map_bytes == map_bytes

    changed = pdu.decode_pdu(bytes.fromhex("0c020800010c1000000e00"))
    assert isinstance(changed, pdu.GearModelHeader)
    state.ingest(changed, Stream.API, time.monotonic() + 1)
    assert state.model_header is changed
    assert state.map_bytes == b""


def test_drivetrain_panel_uses_current_gear_when_map_is_unknown() -> None:
    state = BikeState()
    gear = pdu.decode_pdu(bytes.fromhex("0c01000104220018000000"))
    header = pdu.decode_pdu(bytes.fromhex("0c020800010c0f00040d00"))
    assert isinstance(gear, pdu.GearNotification)
    assert isinstance(header, pdu.GearModelHeader)

    # A header refresh clears the partial map; the current gear notification
    # still has the selected front/rear teeth.
    state.ingest(gear, Stream.API)
    state.ingest(header, Stream.API)
    assert state.drivetrain_view().map_complete is False
    assert state.drivetrain_view().teeth_front == {}
    assert state.drivetrain_view().teeth_rear == {}

    text = drivetrain_panel(state).plain
    assert text.count("34T") >= 2  # front row + current line
    assert text.count("24") >= 2   # rear row + current line


def test_header_reconstructs_dream_drive_coefficients() -> None:
    state = BikeState()
    header = pdu.decode_pdu(bytes.fromhex("0c02080201184701023000"))
    assert isinstance(header, pdu.GearModelHeader)

    state.ingest(header, Stream.API)
    view = state.drivetrain_view()
    assert view.map_complete is True
    assert view.coefficients[1] == 500
    assert view.coefficients[24] == 6999
