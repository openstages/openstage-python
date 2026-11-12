"""Headless dashboard tests using a saved query capture."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Callable
from pathlib import Path
from typing import Any

from textual.containers import Grid
from textual.widgets import Button, Checkbox, Input, Select

from openstage import pdu, uuids
from openstage.client import DrivetrainMode, SB20Client
from openstage.replay import ReplayClient
from openstage.state import API_POWER_SOURCE, FTMS_SOURCE, BikeState
from openstage.tui.app import DashApp, run_snapshot, screen_text, summary
from openstage.wftnp import DEFAULT_PORT

FIXTURES = Path(__file__).parent / "fixtures" / "legacy"


def test_snapshot_renders_typed_values() -> None:
    state = BikeState()
    feed = ReplayClient(FIXTURES / "queries.json", speed=1000)
    text = run_snapshot(
        feed,
        state,
        deque(maxlen=400),
        seconds=0.8,
        size=(120, 40),
        allow_telemetry=False,
    )

    assert "Stages Bike 0071" in text
    assert "rider metrics" in text
    assert "84.00 / 16.00 kg" in text
    assert "1 front × 12 rear" in text
    assert "0x0F" in text
    assert "handlebar controls" in text

    data = summary(state)
    assert data["identity"]["serial"] == "H0603210071"
    assert data["weight"]["value"]["user_kg"] == 84.0


async def test_options_panel_controls_and_defaults() -> None:
    state = BikeState()
    feed = ReplayClient(FIXTURES / "queries.json", speed=1000)
    app = DashApp(feed, state, deque(maxlen=400), allow_telemetry=False)
    async with app.run_test(size=(120, 60)) as pilot:
        await pilot.pause()
        assert state.preferences.power == API_POWER_SOURCE
        assert state.preferences.cadence == API_POWER_SOURCE
        await pilot.press("o")
        await pilot.pause()
        await asyncio.sleep(0.4)
        await pilot.pause()
        text = screen_text(app).lower()
        assert "options" in text
        assert "power/cadence source" in text
        assert "weight" in text
        assert "flywheel brake" in text
        assert "crank length" in text
        assert "buttons (first 10 physical buttons)" in text
        assert "store to flash" in text
        assert len(app.screen.query(Select)) == 13  # source + brake + crank + 10 buttons

        assert app.screen.query_one("#option-weight-user", Input).value == "84"
        assert app.screen.query_one("#option-weight-equipment", Input).value == "16"
        assert app.screen.query_one("#option-brake", Select).value == "true"
        assert app.screen.query_one("#option-crank", Select).value == "165"
        assert app.screen.query_one("#option-button-1", Select).value == "rear_up"
        assert app.screen.query_one("#option-store", Checkbox).value is False
        assert app.screen.query_one("#options-close", Button)
        assert app.screen.query_one("#options-cancel", Button)
        assert app.screen.query_one("#option-buttons", Grid).styles.grid_size_columns == 3

        select = app.screen.query_one("#option-power-cadence", Select)
        select.value = FTMS_SOURCE
        await pilot.pause()
        assert state.preferences.power == FTMS_SOURCE
        assert state.preferences.cadence == FTMS_SOURCE


async def test_more_data_panel_omits_slot_nineteen() -> None:
    state = BikeState()
    feed = ReplayClient(FIXTURES / "queries.json", speed=1000)
    app = DashApp(feed, state, deque(maxlen=400), allow_telemetry=False)
    async with app.run_test(size=(120, 40)) as pilot:
        await asyncio.sleep(0.3)
        await pilot.press("m")
        await pilot.pause()
        await asyncio.sleep(0.4)
        await pilot.pause()
        text = screen_text(app)
        assert "14: " in text
        assert "15: " not in text
        assert "wire slots 1,2,3,6,7,8,11…18" in text


def test_snapshot_of_live_dream_drive_capture() -> None:
    state = BikeState()
    feed = ReplayClient(FIXTURES.parent / "live" / "live-ride.json", speed=1000)
    text = run_snapshot(
        feed,
        state,
        deque(maxlen=400),
        seconds=1.5,
        size=(120, 40),
        allow_telemetry=False,
    )
    assert "coefficients" in text
    assert "coef" in text
    assert "500" in text
    assert "24" in text
    assert state.drivetrain_view().coefficients[4] == 1347


def test_snapshot_of_button_capture() -> None:
    state = BikeState()
    feed = ReplayClient(FIXTURES / "buttons.json", speed=1000)
    text = run_snapshot(
        feed,
        state,
        deque(maxlen=400),
        seconds=0.5,
        size=(120, 40),
        allow_telemetry=False,
    )
    assert "handlebar controls" in text
    assert "events" in text


async def test_options_button_grid_is_responsive() -> None:
    state = BikeState()
    feed = ReplayClient(FIXTURES / "queries.json", speed=1000)
    app = DashApp(feed, state, deque(maxlen=400), allow_telemetry=False)
    async with app.run_test(size=(80, 60)) as pilot:
        await pilot.pause()
        await pilot.press("o")
        await pilot.pause()
        grid = app.screen.query_one("#option-buttons", Grid)
        assert grid.styles.grid_size_columns == 2
        assert grid.styles.grid_size_rows == 5


class _TuiTransport:
    def __init__(self, responses: dict[bytes, list[bytes]]) -> None:
        self.responses = responses
        self.writes: list[bytes] = []
        self.callbacks: dict[str, Callable[[bytes], None]] = {}
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def mtu_size(self) -> int | None:
        return 23 if self._connected else None

    @property
    def service_uuids(self) -> frozenset[str]:
        return frozenset({uuids.API_SERVICE, uuids.FITNESS_MACHINE})

    async def connect(self) -> None:
        self._connected = True

    async def disconnect(self) -> None:
        self._connected = False

    async def read_characteristic(self, uuid: str) -> bytes:
        values = {
            uuids.MODEL_NUMBER: b"SB20",
            uuids.SERIAL_NUMBER: b"H0603210071",
            uuids.FIRMWARE_REVISION: b"1.1",
            uuids.HARDWARE_REVISION: b"0.0",
            uuids.SOFTWARE_REVISION: b"1.12.4+3792",
            uuids.MANUFACTURER_NAME: b"Stages Cycling",
            uuids.DEVICE_NAME: b"Stages Bike 0071",
        }
        if uuid in values:
            return values[uuid]
        raise KeyError(uuid)

    async def write_characteristic(self, uuid: str, data: bytes) -> None:
        assert uuid == uuids.API_REQUEST
        self.writes.append(data)
        callback = self.callbacks.get(uuids.API_RESPONSE)
        for response in self.responses.get(data, []):
            if callback is not None:
                callback(response)

    async def start_notify(self, uuid: str, callback: Callable[[bytes], None]) -> None:
        self.callbacks[uuids.normalize(uuid)] = callback

    async def stop_notify(self, uuid: str) -> None:
        self.callbacks.pop(uuids.normalize(uuid), None)


class _HeaderSequenceTransport(_TuiTransport):
    """Serve a different 0C/2/7 header on each read."""

    def __init__(self, responses: dict[bytes, list[bytes]], headers: list[bytes]) -> None:
        super().__init__(responses)
        self.headers = list(headers)

    async def write_characteristic(self, uuid: str, data: bytes) -> None:
        if data == b"\x0c\x02\x07":
            self.writes.append(data)
            callback = self.callbacks.get(uuids.API_RESPONSE)
            if callback is not None:
                header = (
                    self.headers.pop(0)
                    if len(self.headers) > 1
                    else self.headers[0]
                )
                callback(header)
            return
        await super().write_characteristic(uuid, data)


def _boot_preset_responses() -> dict[bytes, list[bytes]]:
    """Boot resync applies the preset identified by the capture header."""

    plan = (
        SB20Client()
        .gears()
        .set_mode(DrivetrainMode.SIMULATED_CASSETTE)
        .set_front_gears((34,))
        .set_cassette_gears((36, 32, 28, 24, 21, 19, 17, 15, 13, 12, 11, 10))
        .plan()
    )
    return {
        plan.header.pdu: [bytes.fromhex("0c0201")],
        plan.apply.pdu: [bytes.fromhex("0c020c")],
    }


def _tui_responses() -> dict[bytes, list[bytes]]:
    weight_set = bytes.fromhex("030134214006")
    params_set = bytes.fromhex("10000000")
    buttons_set = bytes.fromhex("0b00010405030405030404030102030400")
    grade_zero = bytes.fromhex("0f000000000000000000")
    power_100 = bytes.fromhex("0200c800000000000000")
    power_110 = bytes.fromhex("0200dc00000000000000")
    power_130 = bytes.fromhex("02000401000000000000")
    power_140 = bytes.fromhex("02001801000000000000")
    power_150 = bytes.fromhex("02002c01000000000000")
    crank_set = bytes.fromhex("0a035901")
    zero_reset = bytes.fromhex("0601011ff6ff1400785634123412fbff")
    return {
        b"\x0e\x00": [bytes.fromhex("0e000000000201")],
        b"\x0a\x00": [bytes.fromhex("0a014a0180a91e850a03aeb2c35b6d02")],
        b"\x10\x01": [bytes.fromhex("10010102")],
        b"\x04\x00": [bytes.fromhex("040000")],
        b"\x05\x00": [bytes.fromhex("0500c80000")],
        b"\x03\x00": [bytes.fromhex("0300d0204006")],
        b"\x07\x00": [bytes.fromhex("070050")],
        b"\x0b\x01": [bytes.fromhex("0b01030405030405030404030102030400")],
        b"\x0c\x00\x02": [bytes.fromhex("0c0100010322001c000004")],
        b"\x0c\x02\x07": [bytes.fromhex("0c020800010c0f00040d00")],
        b"\x0d\x02": [bytes.fromhex("0d0280a9312e382e32")],
        b"\x0d\x04": [bytes.fromhex("0d04aeb2312e382e32")],
        weight_set: [weight_set],
        params_set: [params_set],
        buttons_set: [buttons_set],
        grade_zero: [grade_zero],
        power_100: [power_100],
        power_110: [power_110],
        power_130: [power_130],
        power_140: [power_140],
        power_150: [power_150],
        crank_set: [crank_set],
        b"\x06\x00": [zero_reset],
        **_boot_preset_responses(),
    }


async def test_options_close_writes_changed_fields_only() -> None:
    transport = _TuiTransport(_tui_responses())
    client = SB20Client(
        transport=transport,
        request_timeout=1.0,
        subscribe_buttons=False,
        subscribe_ftms=False,
        subscribe_csc=False,
    )
    state = BikeState()
    app = DashApp(client, state, deque(maxlen=400), allow_telemetry=False)
    async with app.run_test(size=(120, 60)) as pilot:
        await asyncio.sleep(0.4)
        await pilot.pause()
        await pilot.press("o")
        await pilot.pause()

        app.screen.query_one("#option-weight-user", Input).value = "85"
        app.screen.query_one("#option-brake", Select).value = "false"
        app.screen.query_one("#option-crank", Select).value = "172.5"
        app.screen.query_one("#option-button-1", Select).value = "front_up"
        await pilot.pause()
        app.screen.query_one("#options-close", Button).press()
        await asyncio.sleep(0.3)
        await pilot.pause()

    assert bytes.fromhex("030134214006") in transport.writes
    assert bytes.fromhex("10000000") in transport.writes
    assert bytes.fromhex("0a035901") in transport.writes
    assert bytes.fromhex("0b00010405030405030404030102030400") in transport.writes
    assert b"\xfd\x00" not in transport.writes
    assert state.weight.value is not None
    assert state.weight.value.user_kg == 85.0
    assert state.parameters.value is not None
    assert state.parameters.value.brake_raw == 0
    assert state.buttons.value is not None
    assert state.buttons.value.values[0] == 1


async def test_erg_controls_on_main_screen() -> None:
    transport = _TuiTransport(_tui_responses())
    client = SB20Client(
        transport=transport,
        request_timeout=1.0,
        subscribe_buttons=False,
        subscribe_ftms=False,
        subscribe_csc=False,
    )
    state = BikeState()
    app = DashApp(client, state, deque(maxlen=400), allow_telemetry=False)
    async with app.run_test(size=(120, 60)) as pilot:
        await asyncio.sleep(0.4)
        await pilot.pause()
        assert bytes.fromhex("0f000000000000000000") in transport.writes
        assert "ERG" in screen_text(app)
        assert "off, target 100 W" in screen_text(app)

        await pilot.press("e")
        await asyncio.sleep(0.4)
        await pilot.pause()
        assert bytes.fromhex("0200c800000000000000") in transport.writes
        assert "on, target 100 W" in screen_text(app)

        await pilot.press("plus")
        await asyncio.sleep(0.4)
        await pilot.pause()
        assert bytes.fromhex("0200dc00000000000000") in transport.writes
        assert "on, target 110 W" in screen_text(app)

        await pilot.press("minus")
        await asyncio.sleep(0.4)
        await pilot.pause()
        assert "on, target 100 W" in screen_text(app)

        await pilot.press("e")
        await asyncio.sleep(0.4)
        await pilot.pause()
        assert "off, target 100 W" in screen_text(app)


async def test_erg_handlebar_buttons_and_hold_repeat() -> None:
    transport = _TuiTransport(_tui_responses())
    client = SB20Client(
        transport=transport,
        request_timeout=1.0,
        subscribe_buttons=True,
        subscribe_ftms=False,
        subscribe_csc=False,
    )
    state = BikeState()
    app = DashApp(client, state, deque(maxlen=400), allow_telemetry=False)
    async with app.run_test(size=(120, 60)) as pilot:
        await asyncio.sleep(0.4)
        await pilot.pause()
        await pilot.press("e")
        await asyncio.sleep(0.4)
        await pilot.pause()
        callback = transport.callbacks[uuids.normalize(uuids.BUTTON_ACTIVE)]

        # Physical slot 1 is assigned REAR_UP: +10 W.
        callback(bytes.fromhex("01000100"))
        await asyncio.sleep(0.15)
        assert app._erg_target_w == 110
        callback(bytes.fromhex("01000100"))
        await asyncio.sleep(0.1)
        assert app._erg_target_w == 110
        callback(bytes.fromhex("08000100"))
        await asyncio.sleep(0.3)
        await pilot.pause()
        assert "on, target 110 W" in screen_text(app)

        # Physical slot 11 is assigned FRONT_UP: +20 W.
        callback(bytes.fromhex("01000004"))
        await asyncio.sleep(0.15)
        callback(bytes.fromhex("08000004"))
        await asyncio.sleep(0.3)
        await pilot.pause()
        assert "on, target 130 W" in screen_text(app)

        # Hold REAR_UP: +10 immediately, then +10 every 0.5 s. The bike
        # repeats the press frame while held, which keeps the debounce fresh.
        callback(bytes.fromhex("01000100"))
        for _ in range(9):
            await asyncio.sleep(0.1)
            callback(bytes.fromhex("01000100"))
        assert app._erg_target_w == 150
        callback(bytes.fromhex("08000100"))
        await asyncio.sleep(0.3)
        await pilot.pause()
        assert "on, target 150 W" in screen_text(app)

        await pilot.press("e")
        await asyncio.sleep(0.4)
        await pilot.pause()
        assert "off, target 150 W" in screen_text(app)


async def test_erg_target_tui_cap_is_500_w() -> None:
    transport = _TuiTransport(_tui_responses())
    client = SB20Client(
        transport=transport,
        request_timeout=1.0,
        subscribe_buttons=False,
        subscribe_ftms=False,
        subscribe_csc=False,
    )
    state = BikeState()
    app = DashApp(client, state, deque(maxlen=400), allow_telemetry=False)
    async with app.run_test(size=(120, 60)) as pilot:
        await asyncio.sleep(0.3)
        await pilot.pause()
        await app._set_erg_target(900)
        assert app._erg_target_w == 500


async def test_tui_polls_bike_status_for_flywheel_rpm() -> None:
    responses = _tui_responses()
    responses[b"\x0e\x00"] = [bytes.fromhex("0e009001000201")]
    transport = _TuiTransport(responses)
    client = SB20Client(
        transport=transport,
        request_timeout=1.0,
        subscribe_buttons=False,
        subscribe_ftms=False,
        subscribe_csc=False,
    )
    state = BikeState()
    app = DashApp(client, state, deque(maxlen=400), allow_telemetry=False)
    async with app.run_test(size=(120, 60)) as pilot:
        await asyncio.sleep(0.4)
        await pilot.pause()
        assert "100.0 rpm" in screen_text(app)
        await asyncio.sleep(2.2)
        await pilot.pause()
        assert transport.writes.count(b"\x0e\x00") >= 2


async def test_zero_reset_overlay_and_success_values() -> None:
    transport = _TuiTransport(_tui_responses())
    client = SB20Client(
        transport=transport,
        request_timeout=1.0,
        subscribe_buttons=False,
        subscribe_ftms=False,
        subscribe_csc=False,
    )
    state = BikeState()
    app = DashApp(client, state, deque(maxlen=400), allow_telemetry=False)
    async with app.run_test(size=(120, 60)) as pilot:
        await asyncio.sleep(0.4)
        await pilot.pause()
        await pilot.press("z")
        await pilot.pause()
        text = screen_text(app).lower()
        assert "zero reset" in text
        assert "both crank arms vertically" in text
        start = app.screen.query_one("#zero-start", Button)
        close = app.screen.query_one("#zero-close", Button)
        assert start.disabled is False
        assert close.disabled is False

        start.press()
        await asyncio.sleep(0.3)
        await pilot.pause()
        text = screen_text(app)
        assert "success" in text.lower()
        assert "left calibration" in text
        assert "-10" in text
        assert "right calibration" in text
        assert "20" in text
        assert app.screen.query_one("#zero-close", Button).disabled is False


async def test_zero_reset_overlay_shows_partial_side_and_global_error() -> None:
    responses = _tui_responses()
    responses[b"\x06\x00"] = [
        bytes.fromhex("060101017e0300000000000000000000"),
        bytes.fromhex("060102"),
    ]
    transport = _TuiTransport(responses)
    client = SB20Client(
        transport=transport,
        request_timeout=1.0,
        subscribe_buttons=False,
        subscribe_ftms=False,
        subscribe_csc=False,
    )
    state = BikeState()
    app = DashApp(client, state, deque(maxlen=400), allow_telemetry=False)
    async with app.run_test(size=(120, 60)) as pilot:
        await asyncio.sleep(0.4)
        await pilot.pause()
        await pilot.press("z")
        await pilot.pause()
        app.screen.query_one("#zero-start", Button).press()
        await asyncio.sleep(0.3)
        await pilot.pause()
        text = screen_text(app).lower()
        assert "error_no_connection" in text
        assert "left calibration" in text
        assert "894" in text
        assert app.screen.query_one("#zero-close", Button).disabled is False


async def test_gearing_screen_preview_in_replay_mode() -> None:
    state = BikeState()
    feed = ReplayClient(FIXTURES / "queries.json", speed=1000)
    app = DashApp(feed, state, deque(maxlen=400), allow_telemetry=False)
    async with app.run_test(size=(120, 60)) as pilot:
        await asyncio.sleep(0.3)
        await pilot.pause()
        await pilot.press("g")
        await pilot.pause()
        text = screen_text(app)
        assert "drivetrain / gearing" in text
        assert "Simulated cassette" in text
        assert "front rings" in text
        assert "cassette" in text
        assert app.screen.query_one("#gear-apply", Button).disabled

        from openstage.gearing_data import load_front_rings

        fronts = load_front_rings()
        front_index = next(i for i, item in enumerate(fronts) if item.name == "1x 34T")
        assert app.screen.query_one("#gear-front", Select).value == str(front_index)


async def test_gearing_screen_live_apply_switches_to_simulation_and_updates_state() -> None:
    boot_header = bytes.fromhex("0c020800010c0f00040d00")
    transport = _HeaderSequenceTransport(_tui_responses(), [boot_header])
    client = SB20Client(
        transport=transport,
        request_timeout=1.0,
        subscribe_buttons=False,
        subscribe_ftms=False,
        subscribe_csc=False,
    )
    plan = client.gears().set_gears_number(24).set_front_shift(2).plan()
    transport.responses[plan.header.pdu] = [bytes.fromhex("0c0201")]
    transport.responses[plan.apply.pdu] = [bytes.fromhex("0c020c")]
    transport.responses[b"\x0c\x02\x03"] = [bytes.fromhex("0c0204")]
    transport.headers = [
        boot_header,
        bytes.fromhex("0c0208") + plan.header.pdu[3:],
    ]

    state = BikeState()
    app = DashApp(client, state, deque(maxlen=400), allow_telemetry=False)
    async with app.run_test(size=(120, 60)) as pilot:
        await asyncio.sleep(0.4)
        await pilot.pause()
        await pilot.press("g")
        await pilot.pause()
        await asyncio.sleep(0.2)
        await pilot.pause()
        assert state.model_header is not None
        assert state.model_header.model is pdu.ModelType.FRONT_REAR_U8
        # The boot matcher identified the stored preset and re-applied it, so
        # the panel shows the full map instead of the broken flash readback.
        assert state.drivetrain_view().map_complete is True
        assert state.drivetrain_view().teeth_rear[3] == 28

        app.screen.query_one("#gear-mode", Select).value = "dream_drive"
        app.screen.query_one("#gear-count", Input).value = "24"
        app.screen.query_one("#gear-step", Input).value = "2"
        await pilot.pause()
        transport.writes.clear()
        app.screen.query_one("#gear-apply", Button).press()
        await asyncio.sleep(0.3)
        await pilot.pause()

        grade_zero = bytes.fromhex("0f000000000000000000")
        assert transport.writes[0] == grade_zero
        # The dashboard's status poll may inject 0e00 between these writes.
        assert [w for w in transport.writes if w != b"\x0e\x00"] == [
            grade_zero,
            plan.header.pdu,
            *(fragment.pdu for fragment in plan.fragments),
            plan.apply.pdu,
            b"\x0c\x00\x02",
        ]
        assert plan.apply.pdu == plan.header.pdu[:2] + b"\x0b" + plan.header.pdu[3:]
        assert "not persistent" in screen_text(app).lower()
        assert state.model_header is not None
        assert state.model_header.model is pdu.ModelType.COEFFICIENTS
        assert state.drivetrain_view().map_complete is True

        stored_from = len(transport.writes)
        app.screen.query_one("#gear-store", Button).press()
        await asyncio.sleep(0.3)
        await pilot.pause()

        stored = [w for w in transport.writes[stored_from:] if w != b"\x0e\x00"]
        assert stored[:2] == [b"\x0c\x02\x03", b"\x0c\x02\x07"]
        assert state.model_header is not None
        assert state.model_header.model is pdu.ModelType.COEFFICIENTS
        assert state.drivetrain_view().map_complete is True
        assert "stored" in screen_text(app).lower()
    await client.close()


async def test_gearing_screen_simulated_cassette_selection_does_not_crash() -> None:
    transport = _TuiTransport(_tui_responses())
    client = SB20Client(
        transport=transport,
        request_timeout=1.0,
        subscribe_buttons=False,
        subscribe_ftms=False,
        subscribe_csc=False,
    )
    state = BikeState()
    app = DashApp(client, state, deque(maxlen=400), allow_telemetry=False)
    async with app.run_test(size=(120, 60)) as pilot:
        await asyncio.sleep(0.4)
        await pilot.pause()
        await pilot.press("g")
        await pilot.pause()

        app.screen.query_one("#gear-mode", Select).value = "simulated_cassette"
        await pilot.pause()
        app.screen.query_one("#gear-front", Select).value = "0"
        await pilot.pause()
        assert "select a cassette" in screen_text(app).lower()

        app.screen.query_one("#gear-cassette", Select).value = "0"
        await pilot.pause()
        text = screen_text(app)
        assert "front:" in text
        assert "cassette:" in text
        assert "total gears:" in text
    await client.close()


async def test_gearing_store_without_apply_keeps_known_map() -> None:
    transport = _TuiTransport(_tui_responses())
    transport.responses[b"\x0c\x02\x03"] = [bytes.fromhex("0c0204")]
    client = SB20Client(
        transport=transport,
        request_timeout=1.0,
        subscribe_buttons=False,
        subscribe_ftms=False,
        subscribe_csc=False,
    )
    state = BikeState()
    app = DashApp(client, state, deque(maxlen=400), allow_telemetry=False)
    async with app.run_test(size=(120, 60)) as pilot:
        await asyncio.sleep(0.4)
        await pilot.pause()
        await pilot.press("g")
        await pilot.pause()
        # A valid preview keeps the store result in the status line.
        app.screen.query_one("#gear-front", Select).value = "0"
        await pilot.pause()
        app.screen.query_one("#gear-cassette", Select).value = "0"
        await pilot.pause()
        # The boot resync (which also refilled the store buffer) supplied the map.
        assert state.drivetrain_view().map_complete is True

        stored_from = len(transport.writes)
        app.screen.query_one("#gear-store", Button).press()
        await asyncio.sleep(0.3)
        await pilot.pause()

        stored = [w for w in transport.writes[stored_from:] if w != b"\x0e\x00"]
        assert stored[:2] == [b"\x0c\x02\x03", b"\x0c\x02\x07"]
        assert "stored; flash header read back" in screen_text(app)
        assert state.drivetrain_view().map_complete is True
    await client.close()


async def test_gearing_failed_transfer_keeps_real_header_in_state() -> None:
    from openstage.gearing_data import load_cassettes, load_front_rings

    transport = _TuiTransport(_tui_responses())
    client = SB20Client(
        transport=transport,
        request_timeout=1.0,
        subscribe_buttons=False,
        subscribe_ftms=False,
        subscribe_csc=False,
    )
    fronts = load_front_rings()
    cassettes = load_cassettes()
    front_index = next(i for i, item in enumerate(fronts) if item.name == "1x 34T")
    cassette_index = next(
        i for i, item in enumerate(cassettes) if item.name.startswith("12/10-36")
    )
    plan = (
        client.gears()
        .set_mode(DrivetrainMode.SIMULATED_CASSETTE)
        .set_front_gears(fronts[front_index].gears)
        .set_cassette_gears(cassettes[cassette_index].gears)
        .plan()
    )
    transport.responses[plan.header.pdu] = [bytes.fromhex("0c0202")]

    state = BikeState()
    app = DashApp(client, state, deque(maxlen=400), allow_telemetry=False)
    async with app.run_test(size=(120, 60)) as pilot:
        await asyncio.sleep(0.4)
        await pilot.pause()
        await pilot.press("g")
        await pilot.pause()
        app.screen.query_one("#gear-mode", Select).value = "simulated_cassette"
        await pilot.pause()
        app.screen.query_one("#gear-front", Select).value = str(front_index)
        app.screen.query_one("#gear-cassette", Select).value = str(cassette_index)
        await pilot.pause()
        app.screen.query_one("#gear-apply", Button).press()
        await asyncio.sleep(0.3)
        await pilot.pause()

        assert state.model_header is not None
        assert state.model_header.model is pdu.ModelType.FRONT_REAR_U8
        assert state.model_header.rear_count == 12
        assert "failed" in screen_text(app).lower()
    await client.close()


async def test_dashboard_serves_the_bridge_when_asked(monkeypatch: Any) -> None:
    import openstage.tui.app as app_module

    started: list[Any] = []
    stopped: list[Any] = []

    class FakeBridge:
        def __init__(self, client: object, **options: Any) -> None:
            self.server = type("Server", (), {"port": 12345})()
            self.options = options
            started.append(self)

        async def start(self) -> None:
            return None

        async def stop(self) -> None:
            stopped.append(self)

        async def handle(self, note: object) -> None:
            return None

    monkeypatch.setattr(app_module, "WftnpBridge", FakeBridge)
    transport = _TuiTransport(_tui_responses())
    client = SB20Client(
        transport=transport,
        request_timeout=1.0,
        subscribe_buttons=False,
        subscribe_ftms=False,
        subscribe_csc=False,
    )
    app = DashApp(
        client, BikeState(), deque(maxlen=400), allow_telemetry=False, wftnp=True
    )
    async with app.run_test(size=(120, 60)) as pilot:
        await asyncio.sleep(0.4)
        await pilot.pause()
        assert len(started) == 1
        assert started[0].options["standalone"] is False
        assert started[0].options["port"] == DEFAULT_PORT
    assert len(stopped) == 1


async def test_dashboard_falls_back_to_an_ephemeral_port(monkeypatch: Any) -> None:
    import openstage.tui.app as app_module

    attempts: list[int] = []

    class BusyBridge:
        def __init__(self, client: object, **options: Any) -> None:
            self.port = options["port"]
            self.server = type("Server", (), {"port": 5000})()
            attempts.append(self.port)

        async def start(self) -> None:
            if self.port != 0:
                raise OSError("address already in use")

        async def stop(self) -> None:
            return None

        async def handle(self, note: object) -> None:
            return None

    monkeypatch.setattr(app_module, "WftnpBridge", BusyBridge)
    transport = _TuiTransport(_tui_responses())
    client = SB20Client(
        transport=transport,
        request_timeout=1.0,
        subscribe_buttons=False,
        subscribe_ftms=False,
        subscribe_csc=False,
    )
    app = DashApp(
        client, BikeState(), deque(maxlen=400), allow_telemetry=False, wftnp=True
    )
    async with app.run_test(size=(120, 60)) as pilot:
        await asyncio.sleep(0.4)
        await pilot.pause()
        assert attempts == [DEFAULT_PORT, 0]
        assert app._bridge is not None
