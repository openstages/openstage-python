"""Textual dashboard for the SB20, built on :mod:`openstage`.

The dashboard consumes the typed ``BikeFeed`` and ``BikeState`` interfaces. It
does not decode raw bytes; the panels only format state values.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import warnings
from collections import deque
from collections.abc import Callable
from typing import Any

from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Grid, Horizontal, Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.screen import ModalScreen, Screen
from textual.widgets import Button, Checkbox, Footer, Header, Input, Label, RichLog, Select, Static

from ..capture import CaptureFormatError
from ..client import (
    BikeError,
    BikeFeed,
    BikeIncompatibleError,
    BikeRadioError,
    BikeSettings,
    BikeSubscriptionError,
    BikeTransientError,
    DrivetrainMode,
    GearPlan,
    GearSettings,
    Notification,
    SB20Client,
    ZeroResetTimeout,
)
from ..cli import add_connection_arguments
from .. import pdu
from ..pdu import SensorFirmware, Stream
from ..replay import ReplayClient
from ..state import API_POWER_SOURCE, FTMS_SOURCE, SOURCE_LABELS, BikeState
from ..gearing_data import load_cassettes, load_front_rings, resync_drivetrain
from ..wftnp import (
    DEFAULT_MAC,
    DEFAULT_NAME,
    DEFAULT_PORT,
    DEFAULT_SERIAL,
    SERVICE,
    WftnpBridge,
    WftnpStartError,
    preflight_port,
)

BUTTON_DEBOUNCE_S = 0.04
ERG_HOLD_DELAY_S = 0.5
ERG_HOLD_REPEAT_S = 0.5
ERG_TARGET_MAX_W = 500
RECONNECT_MAX_DELAY_S = 30.0
RECONNECT_MODAL_AFTER = 5
INCOMPATIBLE_ATTEMPTS = 3


from .panels import (
    buttons_panel,
    drivetrain_panel,
    frames_panel,
    identity_panel,
    live_buttons_panel,
    readback_panel,
    rider_panel,
    status_panel,
)


class MoreDataScreen(ModalScreen[None]):
    """Rarely-changing detail: button configuration and frame counters."""

    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("m", "close", "Close"),
        Binding("q", "close", "Close"),
    ]

    CSS = """
    MoreDataScreen { align: center middle; }
    #more { width: auto; max-width: 96%; height: auto; }
    #more Static {
        width: 1fr; height: auto;
        border: round $primary; background: $surface; padding: 0 1;
    }
    """

    def __init__(self, state: BikeState) -> None:
        super().__init__()
        self.state = state

    def compose(self) -> ComposeResult:
        with Container(id="more"):
            yield Static(id="more-buttons")
            yield Static(id="more-frames")

    def on_mount(self) -> None:
        self.query_one("#more-buttons").border_title = "buttons — read-back (0x0B/1)"
        self.query_one("#more-frames").border_title = "frames received"
        self._refresh()
        self.set_interval(0.5, self._refresh)

    def _refresh(self) -> None:
        self.query_one("#more-buttons", Static).update(buttons_panel(self.state))
        self.query_one("#more-frames", Static).update(frames_panel(self.state))

    def action_close(self) -> None:
        self.dismiss(None)


class OptionsScreen(ModalScreen[None]):
    """Full-screen options: source, weight, brake, buttons, optional store."""

    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("o", "close", "Close"),
        Binding("q", "close", "Close"),
    ]

    CSS = """
    OptionsScreen { align: center middle; }
    #options {
        width: 100%; height: 100%;
        border: round $primary; background: $surface; padding: 1 2;
    }
    #options-title { text-style: bold; }
    #options-hint { color: $text-muted; }
    .option-section { text-style: bold; margin-top: 1; }
    .option-row { height: auto; }
    .option-label { width: 26; padding-top: 1; }
    #options Select { width: 46; }
    #options Input { width: 24; }
    #option-buttons { height: auto; grid-columns: 1fr; grid-gutter: 0 1; }
    .button-cell { height: auto; width: 1fr; }
    .button-label { width: 10; padding-top: 1; }
    .button-cell Select { width: 1fr; }
    #options-actions { height: auto; margin-top: 1; }
    #options-status { margin-top: 1; color: $text-muted; }
    """

    BUTTON_OPTIONS = [
        ("front up", "front_up"),
        ("front down", "front_down"),
        ("rear up", "rear_up"),
        ("rear down", "rear_down"),
        ("not assigned / external", "not_assigned"),
    ]

    def __init__(self, state: BikeState, feed: BikeFeed) -> None:
        super().__init__()
        self.state = state
        self.feed = feed
        self._live = isinstance(feed, SB20Client)
        self.settings: BikeSettings | None = (
            feed.settings() if isinstance(feed, SB20Client) else None
        )
        self._button_baseline: dict[int, pdu.ButtonAction | None] = {}

    def compose(self) -> ComposeResult:
        current = self.state.preferences.power
        if current not in (API_POWER_SOURCE, FTMS_SOURCE):
            current = API_POWER_SOURCE
        weight = self.state.weight.value
        parameters = self.state.parameters.value
        brake_value = "true" if parameters is None or parameters.brake_raw != 0 else "false"
        pairing = self.state.pairing.value
        current_crank = pairing.crank_length_mm if pairing is not None else None
        crank_value = (
            f"{current_crank:g}"
            if current_crank is not None and current_crank in pdu.CRANK_LENGTHS_MM
            else None
        )
        buttons = self.state.buttons.value
        if buttons is not None:
            actions = pdu.button_actions_from_wire(buttons.values)
            self._button_baseline = {
                physical: actions[index] if index < len(actions) else None
                for index, physical in enumerate(range(1, 11))
            }

        with VerticalScroll(id="options"):
            yield Static("options", id="options-title")
            yield Static(
                "source is display-only; weight, brake and buttons are live writes. "
                "Flash storage is off unless you check it.",
                id="options-hint",
            )
            if not self.state.csc_available:
                yield Static(
                    "CSC unavailable; cadence uses API/FTMS",
                    id="options-csc",
                )
            with Horizontal(classes="option-row"):
                yield Label("power/cadence source", classes="option-label")
                yield Select(
                    [
                        (SOURCE_LABELS[API_POWER_SOURCE], API_POWER_SOURCE),
                        (SOURCE_LABELS[FTMS_SOURCE], FTMS_SOURCE),
                    ],
                    value=current,
                    allow_blank=False,
                    id="option-power-cadence",
                )

            yield Static("weight", classes="option-section")
            with Horizontal(classes="option-row"):
                yield Label("user weight (kg)", classes="option-label")
                yield Input(
                    value="" if weight is None else f"{weight.user_kg:g}",
                    id="option-weight-user",
                    type="number",
                )
            with Horizontal(classes="option-row"):
                yield Label("equipment weight (kg)", classes="option-label")
                yield Input(
                    value="" if weight is None else f"{weight.equipment_kg:g}",
                    id="option-weight-equipment",
                    type="number",
                )

            yield Static("brake", classes="option-section")
            with Horizontal(classes="option-row"):
                yield Label("flywheel brake", classes="option-label")
                yield Select(
                    [("enabled", "true"), ("disabled", "false")],
                    value=brake_value,
                    allow_blank=False,
                    id="option-brake",
                )

            yield Static("crank length", classes="option-section")
            with Horizontal(classes="option-row"):
                yield Label("crank length (mm)", classes="option-label")
                yield Select(
                    [(f"{mm:g}", f"{mm:g}") for mm in pdu.CRANK_LENGTHS_MM],
                    value=crank_value,
                    allow_blank=True,
                    id="option-crank",
                )

            yield Static("buttons (first 10 physical buttons)", classes="option-section")
            with Grid(id="option-buttons"):
                for physical in range(1, 11):
                    action = self._button_baseline.get(physical)
                    value = "not_assigned" if action is None else action.name.lower()
                    with Horizontal(classes="button-cell"):
                        yield Label(f"button {physical}", classes="button-label")
                        yield Select(
                            self.BUTTON_OPTIONS,
                            value=value,
                            allow_blank=False,
                            id=f"option-button-{physical}",
                        )

            yield Checkbox(
                "Store to flash (survives power cycle)",
                value=False,
                id="option-store",
            )
            yield Static(
                "" if self._live else "replay mode: writes disabled",
                id="options-status",
            )
            with Horizontal(id="options-actions"):
                yield Button("Close & apply", id="options-close", variant="primary",
                             disabled=not self._live)
                yield Button("Close without writing", id="options-cancel",
                             disabled=not self._live)

    def on_mount(self) -> None:
        self._layout_button_grid()
        if self._live:
            self._status("edit values, then close to apply. Flash storage is off by default.")

    def on_resize(self, event: events.Resize) -> None:
        self._layout_button_grid()

    def _layout_button_grid(self) -> None:
        if not self.is_mounted:
            return
        grid = self.query_one("#option-buttons", Grid)
        columns = max(1, min(3, self.size.width // 34))
        grid.styles.grid_size_columns = columns
        grid.styles.grid_size_rows = (10 + columns - 1) // columns

    def _status(self, text: str) -> None:
        self.query_one("#options-status", Static).update(text)

    def on_select_changed(self, event: Select.Changed) -> None:
        widget_id = event.select.id
        if widget_id != "option-power-cadence":
            return
        value = str(event.value)
        if value in (API_POWER_SOURCE, FTMS_SOURCE):
            self.state.preferences.power = value
            self.state.preferences.cadence = value

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "options-cancel":
            self.dismiss(None)
            return
        if event.button.id == "options-close":
            await self._apply_and_close()

    async def _apply_and_close(self) -> None:
        if not self._live:
            self.dismiss(None)
            return
        if await self._apply():
            self.dismiss(None)

    async def _apply(self) -> bool:
        if self.settings is None:
            self._status("replay mode: writes disabled")
            return False
        try:
            user, equipment = self._read_weight_inputs()
            self.settings.set_weight(user, equipment)

            brake = str(self.query_one("#option-brake", Select).value) == "true"
            self.settings.set_brake(brake)

            crank = self.query_one("#option-crank", Select).value
            if crank is not Select.BLANK:
                self.settings.set_crank_length(float(str(crank)))

            for physical in range(1, 11):
                action = pdu.parse_button_action(
                    str(self.query_one(f"#option-button-{physical}", Select).value)
                )
                self.settings.set_button_slots({physical: action})

            store = self.query_one("#option-store", Checkbox).value
            commit = await self.settings.commit(store=store)
            for operation in commit.operations:
                self.state.ingest(operation.set_response, Stream.API)
            if self._button_baseline:
                buttons = self.state.buttons.value
                if buttons is not None:
                    actions = pdu.button_actions_from_wire(buttons.values)
                    self._button_baseline = {
                        physical: actions[index] if index < len(actions) else None
                        for index, physical in enumerate(range(1, 11))
                    }

            if commit.writes == 0:
                self._status("no changes; nothing written")
                return True
            if commit.persisted:
                self._status(f"applied {commit.writes} write(s); stored to flash")
                return True
            if store:
                self._status(f"applied {commit.writes} write(s); store not confirmed")
                return False
            self._status(f"applied {commit.writes} write(s); not stored (RAM only)")
            return True
        except (BikeError, ValueError) as exc:
            self._status(f"error: {exc}")
            return False

    def _read_weight_inputs(self) -> tuple[float, float]:
        return (
            self._input_float("option-weight-user"),
            self._input_float("option-weight-equipment"),
        )

    def _input_float(self, widget_id: str) -> float:
        text = self.query_one(f"#{widget_id}", Input).value.strip()
        if not text:
            raise ValueError(f"{widget_id} is empty")
        value = float(text)
        if value < 0:
            raise ValueError(f"{widget_id} must be non-negative")
        return value

    async def action_close(self) -> None:
        await self._apply_and_close()


class ZeroResetScreen(ModalScreen[None]):
    """Zero-reset calibration overlay.

    Starting the reset disables closing; the bike has no cancel PDU, so the
    overlay waits for a terminal status before allowing the user to leave.
    """

    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("z", "close", "Close"),
    ]

    CSS = """
    ZeroResetScreen { align: center middle; }
    #zero-reset {
        width: 76; height: auto;
        border: round $primary; background: $surface; padding: 1 2;
    }
    #zero-title { text-style: bold; margin-bottom: 1; }
    #zero-instructions { margin-bottom: 1; }
    #zero-status { margin-top: 1; }
    #zero-actions { height: auto; margin-top: 1; }
    """

    def __init__(self, feed: BikeFeed) -> None:
        super().__init__()
        self.feed = feed
        self._live = isinstance(feed, SB20Client)
        self._started = False
        self._finished = False
        self._task: asyncio.Task[None] | None = None

    def compose(self) -> ComposeResult:
        with Container(id="zero-reset"):
            yield Static("zero reset / calibration", id="zero-title")
            yield Static(
                "Position both crank arms vertically, then press Start. "
                "The reset cannot be cancelled after it starts.",
                id="zero-instructions",
            )
            yield Static(
                "" if self._live else "replay mode: zero reset disabled",
                id="zero-status",
            )
            with Horizontal(id="zero-actions"):
                yield Button("Start", id="zero-start", variant="primary",
                             disabled=not self._live)
                yield Button("Close", id="zero-close")

    def _set_status(self, text: str) -> None:
        self.query_one("#zero-status", Static).update(text)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "zero-start":
            self._start()
        elif event.button.id == "zero-close":
            self.action_close()

    def _start(self) -> None:
        if self._started or not self._live:
            return
        self._started = True
        self._finished = False
        self.query_one("#zero-start", Button).disabled = True
        self.query_one("#zero-close", Button).disabled = True
        self._set_status("reset started; waiting for the bike…")
        self._task = asyncio.create_task(self._run_reset())

    async def _run_reset(self) -> None:
        feed = self.feed
        assert isinstance(feed, SB20Client)
        try:
            self._finish(await feed.zero_reset())
        except ZeroResetTimeout as exc:
            self._finish(exc.result, heading="reset timed out (global)")
        except asyncio.CancelledError:
            raise
        except BikeError as exc:
            self._finish_error(str(exc))

    def _finish(self, result: pdu.ZeroReset, *, heading: str | None = None) -> None:
        self._finished = True
        self.query_one("#zero-close", Button).disabled = False
        lines: list[str] = []
        if heading is not None:
            lines.append(heading)
        elif result.status is pdu.ZeroResetStatus.SUCCESS:
            lines.append("success")
        else:
            lines.append(f"reset failed: {result.status.name.lower()} (global)")
        if (
            result.left_offset is not None
            or result.right_offset is not None
            or result.torque_offset is not None
        ):
            left = "—" if result.left_offset is None else str(result.left_offset)
            right = "—" if result.right_offset is None else str(result.right_offset)
            torque = "—" if result.torque_offset is None else str(result.torque_offset)
            lines.extend(
                (
                    f"left calibration:  {left}",
                    f"right calibration: {right}",
                    f"torque offset:     {torque}",
                )
            )
        self._set_status("\n".join(lines))

    def _finish_error(self, message: str) -> None:
        self._finished = True
        self.query_one("#zero-close", Button).disabled = False
        self._set_status(f"reset failed: {message}")

    def action_close(self) -> None:
        if self._started and not self._finished:
            return
        self.dismiss(None)

    async def on_unmount(self) -> None:
        if self._task is not None:
            self._task.cancel()


class ReconnectScreen(ModalScreen[bool]):
    """Non-blocking notice that the bike link is being retried."""

    BINDINGS = [
        Binding("escape", "retry", "Retry"),
        Binding("r", "retry", "Retry"),
        Binding("q", "quit", "Quit"),
    ]

    CSS = """
    ReconnectScreen { align: center middle; }
    #reconnect {
        width: 72; height: auto;
        border: round $warning; background: $surface; padding: 1 2;
    }
    #reconnect-message { margin-top: 1; }
    #reconnect-actions { height: auto; margin-top: 1; }
    """

    def __init__(self, message: str) -> None:
        super().__init__()
        self.message = message

    def compose(self) -> ComposeResult:
        with Container(id="reconnect"):
            yield Static("bike connection unavailable", id="reconnect-title")
            yield Static(self.message, id="reconnect-message")
            yield Static(
                "The dashboard keeps retrying in the background.",
                id="reconnect-note",
            )
            with Horizontal(id="reconnect-actions"):
                yield Button("Retry", id="reconnect-retry", variant="primary")
                yield Button("Quit", id="reconnect-quit")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "reconnect-quit":
            self.dismiss(False)
        else:
            self.dismiss(True)

    def action_retry(self) -> None:
        self.dismiss(True)

    def action_quit(self) -> None:
        self.dismiss(False)


class GearingScreen(ModalScreen[None]):
    """Drivetrain configuration; write-only, with a header/map preview."""

    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("g", "close", "Close"),
    ]

    CSS = """
    GearingScreen { align: center middle; }
    #gearing {
        width: 100%; height: 100%;
        border: round $primary; background: $surface; padding: 1 2;
    }
    #gear-form { height: 1fr; }
    #gear-dd, #gear-cassette-fields { height: auto; }
    #gear-title { text-style: bold; }
    #gear-hint { color: $text-muted; }
    #gear-current { margin-top: 1; }
    .gear-row { height: auto; }
    .gear-label { width: 22; padding-top: 1; }
    #gearing Select { width: 62; }
    #gearing Input { width: 28; }
    #gear-preview { margin-top: 1; height: auto; }
    #gear-status { margin-top: 1; color: $text-muted; }
    #gear-actions { height: auto; margin-top: 1; }
    #gear-actions Button { margin-right: 1; }
    """

    def __init__(
        self,
        state: BikeState,
        feed: BikeFeed,
        *,
        on_simulation: Callable[[], None] | None = None,
        on_log: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__()
        self.state = state
        self.feed = feed
        self._on_simulation = on_simulation
        self._on_log = on_log
        self._live = isinstance(feed, SB20Client)
        self._busy = False
        self._touched = False
        self._front_prefilled = False
        self._status_kind = "apply"
        self._applied_plan: GearPlan | None = None
        self._data_error: str | None = None
        try:
            self._front_sets = load_front_rings()
            self._cassette_sets = load_cassettes()
        except ValueError as exc:
            self._front_sets = ()
            self._cassette_sets = ()
            self._data_error = str(exc)

    def compose(self) -> ComposeResult:
        front_options = [(item.name, str(index)) for index, item in enumerate(self._front_sets)]
        cassette_options = [
            (item.name, str(index)) for index, item in enumerate(self._cassette_sets)
        ]
        with Vertical(id="gearing"):
            with VerticalScroll(id="gear-form"):
                yield Static("drivetrain / gearing", id="gear-title")
                yield Static(
                    "Write-only; the current map cannot be read back.",
                    id="gear-hint",
                )
                yield Static(self._current_summary(self.state.model_header), id="gear-current")
                with Horizontal(classes="gear-row"):
                    yield Label("mode", classes="gear-label")
                    yield Select(
                        [
                            ("Dream Drive", DrivetrainMode.DREAM_DRIVE.value),
                            ("Simulated cassette", DrivetrainMode.SIMULATED_CASSETTE.value),
                        ],
                        value=DrivetrainMode.DREAM_DRIVE.value,
                        allow_blank=False,
                        id="gear-mode",
                    )

                with Container(id="gear-dd"):
                    with Horizontal(classes="gear-row"):
                        yield Label("gears (10–50)", classes="gear-label")
                        yield Input(value="24", type="number", id="gear-count")
                    with Horizontal(classes="gear-row"):
                        yield Label("front shift (0–9)", classes="gear-label")
                        yield Input(value="2", type="number", id="gear-step")

                with Container(id="gear-cassette-fields"):
                    with Horizontal(classes="gear-row"):
                        yield Label("front rings", classes="gear-label")
                        yield Select(
                            front_options,
                            value=Select.NULL,
                            allow_blank=True,
                            id="gear-front",
                        )
                    with Horizontal(classes="gear-row"):
                        yield Label("cassette", classes="gear-label")
                        yield Select(
                            cassette_options,
                            value=Select.NULL,
                            allow_blank=True,
                            id="gear-cassette",
                        )
                    with Horizontal(classes="gear-row"):
                        yield Label("custom front", classes="gear-label")
                        yield Input(
                            placeholder="40 or 34,50",
                            id="gear-front-custom",
                        )

                yield Static("", id="gear-preview")
                with Horizontal(id="gear-actions"):
                    yield Button("Apply (RAM)", id="gear-apply", variant="primary",
                                 compact=True, disabled=not self._live)
                    yield Button("Store to flash", id="gear-store",
                                 compact=True, disabled=not self._live)
                    yield Button("Close", id="gear-close", compact=True)
            yield Static(
                "" if self._live else "replay mode: gearing writes disabled",
                id="gear-status",
            )

    def on_mount(self) -> None:
        if self._data_error is not None:
            self._status(f"database error: {self._data_error}")
        header = self.state.model_header
        if header is not None:
            self._current_label().update(self._current_summary(header))
            if not self._touched:
                self._apply_header_defaults(header)
        self._update_mode_visibility()
        self._prefill_front_from_gear()
        self._refresh_preview()

    def _current_label(self) -> Static:
        return self.query_one("#gear-current", Static)

    def _status(self, text: str, *, kind: str = "apply") -> None:
        self._status_kind = kind
        if kind == "apply" and self._on_log is not None:
            self._on_log(f"gearing: {text}")
        if not self.is_mounted:
            return
        self.query_one("#gear-status", Static).update(text)

    def _current_summary(self, header: pdu.GearModelHeader | None) -> str:
        if header is None:
            return "current: header unavailable (0C/02/07)"
        drive = "Dream Drive" if header.dream_drive else "non-DD"
        model = header.model.name.lower().replace("_", " ")
        return (
            f"current: model {model}, "
            f"{header.front_count}×{header.rear_count}, CRC 0x{header.crc8:02X}, "
            f"{drive}, step {header.dream_drive_step}, map {header.map_size} B"
        )

    def _apply_header_defaults(self, header: pdu.GearModelHeader) -> None:
        if header.model is not pdu.ModelType.COEFFICIENTS or not header.dream_drive:
            self.query_one("#gear-mode", Select).value = (
                DrivetrainMode.SIMULATED_CASSETTE.value
            )
            return
        self.query_one("#gear-mode", Select).value = DrivetrainMode.DREAM_DRIVE.value
        if pdu.DREAM_DRIVE_GEARS_MIN <= header.rear_count <= pdu.DREAM_DRIVE_GEARS_MAX:
            self.query_one("#gear-count", Input).value = str(header.rear_count)
        if pdu.DREAM_DRIVE_STEP_MIN <= header.dream_drive_step <= pdu.DREAM_DRIVE_STEP_MAX:
            self.query_one("#gear-step", Input).value = str(header.dream_drive_step)

    def _update_mode_visibility(self) -> None:
        mode = self.query_one("#gear-mode", Select).value
        dream_drive = str(mode) == DrivetrainMode.DREAM_DRIVE.value
        self.query_one("#gear-dd").styles.display = "block" if dream_drive else "none"
        self.query_one("#gear-cassette-fields").styles.display = (
            "none" if dream_drive else "block"
        )

    def _prefill_front_from_gear(self) -> None:
        if self._front_prefilled:
            return
        header = self.state.model_header
        gear = self.state.gear.value
        if header is None or header.model is not pdu.ModelType.FRONT_REAR_U8:
            return
        if header.front_count != 1 or gear is None:
            return
        if gear.model is not pdu.ModelType.FRONT_REAR_U8 or not gear.front_teeth:
            return
        select = self.query_one("#gear-front", Select)
        custom = self.query_one("#gear-front-custom", Input)
        if select.value is not Select.NULL or custom.value.strip():
            return
        teeth = gear.front_teeth
        index = next(
            (
                i
                for i, item in enumerate(self._front_sets)
                if len(item.gears) == 1 and item.gears[0] == teeth
            ),
            None,
        )
        if index is None:
            custom.value = str(teeth)
        else:
            select.value = str(index)
        self._front_prefilled = True

    def _selected_settings(self) -> GearSettings:
        assert isinstance(self.feed, SB20Client)
        mode = DrivetrainMode(str(self.query_one("#gear-mode", Select).value))
        settings = self.feed.gears().set_mode(mode)
        if mode is DrivetrainMode.DREAM_DRIVE:
            settings.set_gears_number(self._input_int("gear-count"))
            settings.set_front_shift(self._input_int("gear-step"))
            return settings

        custom = self.query_one("#gear-front-custom", Input).value.strip()
        if custom:
            front = self._parse_teeth(custom)
        else:
            value = self.query_one("#gear-front", Select).value
            if value is Select.NULL:
                raise ValueError("select a front ring set or enter a custom value")
            front = self._front_sets[int(str(value))].gears
        cassette_value = self.query_one("#gear-cassette", Select).value
        if cassette_value is Select.NULL:
            raise ValueError("select a cassette")
        cassette = self._cassette_sets[int(str(cassette_value))].gears
        return settings.set_front_gears(front).set_cassette_gears(cassette)

    def _input_int(self, widget_id: str) -> int:
        text = self.query_one(f"#{widget_id}", Input).value.strip()
        if not text:
            raise ValueError(f"{widget_id} is empty")
        return int(text)

    @staticmethod
    def _parse_teeth(text: str) -> tuple[int, ...]:
        parts = text.replace(",", " ").split()
        if not parts:
            raise ValueError("custom front is empty")
        return tuple(int(part) for part in parts)

    def _build_plan(self) -> tuple[GearSettings | None, GearPlan | None, str | None]:
        if not self._live:
            return None, None, "replay mode: gearing writes disabled"
        try:
            settings = self._selected_settings()
            return settings, settings.plan(), None
        except (BikeError, ValueError) as exc:
            return None, None, str(exc)

    def _refresh_preview(self) -> None:
        if not self.is_mounted:
            return
        _settings, plan, error = self._build_plan()
        preview = self.query_one("#gear-preview", Static)
        apply_button = self.query_one("#gear-apply", Button)
        if error is not None or plan is None:
            preview.update("")
            if not self._busy:
                self._status(f"error: {error}", kind="preview")
            apply_button.disabled = True
            return
        if self._status_kind == "preview":
            self._status("", kind="preview")
        lines: list[str] = []
        if plan.dream_drive:
            lines.append(
                f"Dream Drive: {plan.rear_count} gears, big shift {plan.step}"
            )
            lines.append(
                f"coefficients: {plan.coefficients[0]} … {plan.coefficients[-1]}"
            )
        else:
            lines.append(f"front: {'/'.join(map(str, plan.front_gears))} T")
            lines.append(
                f"cassette: {'/'.join(map(str, plan.cassette_gears))} T"
            )
            lines.append(f"total gears: {plan.total_gears}")
        lines.append(
            f"map {plan.map_size} B, CRC 0x{plan.crc8:02X}, "
            f"{len(plan.fragments)} fragment(s)"
        )
        lines.append(f"header: {plan.header_pdu.hex()}")
        preview.update("\n".join(lines))
        apply_button.disabled = not self._live or self._busy

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        if not self.is_mounted:
            return
        self.query_one("#gear-close", Button).disabled = busy
        self.query_one("#gear-apply", Button).disabled = busy or not self._live
        self.query_one("#gear-store", Button).disabled = busy or not self._live
        if not busy:
            self._refresh_preview()

    async def _apply_settings(self, settings: GearSettings, label: str) -> None:
        if not self._live or self._busy:
            return
        assert isinstance(self.feed, SB20Client)
        self._set_busy(True)
        try:
            self._status(f"applying {label} to RAM…")
            if self._on_simulation is not None:
                self._on_simulation()
            result = await settings.commit()
            self._applied_plan = result.plan
            header = result.plan.as_header()
            self.state.set_known_drivetrain(header, result.plan.map_bytes)
            self._current_label().update(self._current_summary(header))
            try:
                self.state.ingest(await self.feed.get_current_gear(), Stream.API)
            except BikeError:
                pass
            self._status(
                f"{label}: applied to RAM only (not persistent); "
                f"map {result.plan.map_size} B, CRC 0x{result.plan.crc8:02X}; "
                f"press Store to flash to keep it"
            )
        except (BikeError, ValueError) as exc:
            self._status(f"{label} failed: {exc}")
        finally:
            self._set_busy(False)

    async def _store_flash(self) -> None:
        if not self._live or self._busy:
            return
        assert isinstance(self.feed, SB20Client)
        self._set_busy(True)
        try:
            self._status("storing applied map to flash (no API traffic)…")
            result = await self.feed.store_gearing()
            if not result.stored:
                if result.ack is not None:
                    self._status(f"store failed: {result.error}")
                else:
                    self._status(
                        "store not confirmed: "
                        + (result.error or "no 0x0C/2/4 or 6")
                        + "; do not re-apply while flash may be writing"
                    )
                return
            header = result.header_after
            if header is None:
                self._status("stored; flash header readback unavailable")
                return
            plan = self._applied_plan
            if plan is not None and plan.matches(header):
                self.state.set_known_drivetrain(header, plan.map_bytes)
                self._status("stored; flash header verified")
            else:
                self.state.ingest(header, Stream.API)
                self._status("stored; flash header read back")
            self._current_label().update(self._current_summary(header))
        except BikeError as exc:
            self._status(f"store failed: {exc}")
        finally:
            self._set_busy(False)

    def on_select_changed(self, event: Select.Changed) -> None:
        self._touched = True
        if event.select.id == "gear-mode":
            self._update_mode_visibility()
            self._prefill_front_from_gear()
        self._refresh_preview()

    def on_input_changed(self, event: Input.Changed) -> None:
        self._touched = True
        self._refresh_preview()

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "gear-close":
            self.action_close()
            return
        if self._busy or not self._live:
            return
        if event.button.id == "gear-apply":
            settings, _plan, error = self._build_plan()
            if settings is None:
                self._status(f"error: {error}")
                return
            await self._apply_settings(settings, "gearing")
            return
        if event.button.id == "gear-store":
            await self._store_flash()

    def action_close(self) -> None:
        if self._busy:
            return
        self.dismiss(None)


class DashApp(App[None]):
    TITLE = "Stages SB20"

    CSS = """
    Screen { layout: vertical; }
    #identity {
        width: 1fr; height: auto;
        border: round $primary; padding: 0 1;
    }
    #livebuttons {
        width: 1fr; height: auto;
        border: round $success; padding: 0 1;
    }
    #panels {
        layout: grid;
        grid-size: 2 2;
        grid-columns: 1fr 1fr;
        grid-rows: auto auto;
        height: auto;
    }
    #panels > Static {
        width: 1fr; height: 100%;
        border: round $primary; padding: 0 1;
    }
    #log { height: 1fr; min-height: 4; border: round $accent; padding: 0 1; }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "resend", "Re-send queries"),
        Binding("o", "options", "Options"),
        Binding("g", "gearing", "Gearing"),
        Binding("d", "gearing", "Gearing", show=False),
        Binding("m", "more_data", "More data"),
        Binding("z", "zero_reset", "Zero reset"),
        Binding("e", "toggle_erg", "ERG on/off"),
        Binding("plus", "erg_up", "ERG +10 W"),
        Binding("minus", "erg_down", "ERG -10 W"),
        Binding("c", "clear_log", "Clear log"),
    ]

    def __init__(
        self,
        feed: BikeFeed,
        state: BikeState,
        log_lines: deque[str],
        *,
        allow_telemetry: bool,
        wftnp: bool = False,
        wftnp_port: int | None = None,
    ) -> None:
        super().__init__()
        self.feed = feed
        self.state = state
        self.log_lines = log_lines
        self.allow_telemetry = allow_telemetry
        self._wftnp = wftnp
        self._wftnp_port = DEFAULT_PORT if wftnp_port is None else wftnp_port
        self._bridge: WftnpBridge | None = None
        self._logged = 0
        self._feed_task: asyncio.Task[None] | None = None
        self._status_task: asyncio.Task[None] | None = None
        self._main_screen: Screen[object] | None = None
        self._erg_on = False
        self._erg_target_w = 100
        self._button_state: set[int] = set()
        self._button_generation: dict[int, int] = {}
        self._button_tasks: set[asyncio.Task[None]] = set()
        self._connected = False
        self._reconnect_modal_open = False
        self._cleanup_errors: list[str] = []

    @property
    def cleanup_errors(self) -> tuple[str, ...]:
        return tuple(self._cleanup_errors)

    # ------------------------------------------------------------------ layout

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static(id="identity")
        with Grid(id="panels"):
            yield Static(id="rider")
            yield Static(id="drivetrain")
            yield Static(id="status")
            yield Static(id="readback")
        yield Static(id="livebuttons")
        yield RichLog(id="log", max_lines=400, markup=False, wrap=True)
        yield Footer()

    def on_mount(self) -> None:
        self._main_screen = self.screen
        self.sub_title = self.feed.mode
        for widget_id, title in (
            ("#identity", "device"),
            ("#rider", "rider metrics"),
            ("#drivetrain", "drivetrain — active map"),
            ("#status", "bike status"),
            ("#readback", "read-back (queries only, nothing set)"),
            ("#livebuttons", "handlebar controls (live, 0c46be60)"),
            ("#log", "event log"),
        ):
            self.query_one(widget_id).border_title = title
        self._feed_task = asyncio.create_task(self._run_feed())
        self.set_interval(0.25, self._refresh)

    # ------------------------------------------------------------------ data

    async def _run_feed(self) -> None:
        try:
            if isinstance(self.feed, SB20Client):
                await self._run_live_feed()
            else:
                await self._run_replay_feed()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Task boundary: let Textual render the traceback instead of
            # flattening an unexpected bug into a log line.
            self._handle_exception(exc)

    async def _run_replay_feed(self) -> None:
        feed = self.feed
        await feed.connect()
        self.state.identity = feed.identity
        self.state.mtu = feed.mtu
        self.state.status = feed.status
        results = await feed.query_all()
        self._ingest_sensor_sides(results)
        async for note in feed.notifications():
            await self._consume(note)
        self.state.status = feed.status

    async def _run_live_feed(self) -> None:
        feed = self.feed
        assert isinstance(feed, SB20Client)
        failures = 0
        incompatible = 0
        while True:
            try:
                await feed.connect()
            except BikeRadioError as exc:
                self._fatal(f"Bluetooth adapter unavailable: {exc}")
                return
            except BikeIncompatibleError as exc:
                incompatible += 1
                if incompatible >= INCOMPATIBLE_ATTEMPTS:
                    self._fatal(f"cannot use this device: {exc}")
                    return
                self._event(
                    f"{exc}; retrying connection "
                    f"({incompatible}/{INCOMPATIBLE_ATTEMPTS - 1})"
                )
                await asyncio.sleep(1.0)
                continue
            except BikeTransientError as exc:
                failures += 1
                self._event(f"connection failed: {exc}; retrying")
                self.state.status = "reconnecting…"
                if failures >= RECONNECT_MODAL_AFTER:
                    self._show_reconnect_modal(str(exc))
                await asyncio.sleep(self._backoff(failures))
                continue
            except BikeError as exc:
                self._fatal(f"cannot connect to the bike: {exc}")
                return

            self._connected = True
            failures = 0
            incompatible = 0
            try:
                await self._after_connect(feed)
                async for note in feed.notifications():
                    await self._consume(note)
            except WftnpStartError as exc:
                self._connected = False
                self._fatal(f"WFTNP endpoint failed: {exc}")
                return
            except BikeRadioError as exc:
                self._connected = False
                self._fatal(f"Bluetooth adapter unavailable: {exc}")
                return
            except BikeIncompatibleError as exc:
                self._connected = False
                incompatible += 1
                if incompatible >= INCOMPATIBLE_ATTEMPTS:
                    self._fatal(f"cannot use this device: {exc}")
                    return
                self._event(
                    f"{exc}; retrying connection "
                    f"({incompatible}/{INCOMPATIBLE_ATTEMPTS - 1})"
                )
                await asyncio.sleep(1.0)
                continue
            except BikeTransientError as exc:
                self._connected = False
                failures += 1
                self._event(f"bike link lost: {exc}; reconnecting")
                self.state.status = "reconnecting…"
                if failures >= RECONNECT_MODAL_AFTER:
                    self._show_reconnect_modal(str(exc))
                await asyncio.sleep(self._backoff(failures))
                continue
            except BikeError as exc:
                self._connected = False
                self._fatal(f"bike error: {exc}")
                return
            else:
                self._connected = False
                return

    async def _after_connect(self, feed: SB20Client) -> None:
        self.state.identity = feed.identity
        self.state.mtu = feed.mtu
        self.state.status = feed.status
        self.state.csc_available = feed.csc_available
        try:
            await feed.set_target_grade(0.0)
            self.log_lines.append("control: simulation at 0.0 % grade")
        except BikeError as exc:
            self.log_lines.append(f"initial simulation mode failed: {exc}")

        boot_fragments: list[bytes] = []

        def _collect_fragment(note: Notification) -> None:
            if isinstance(note.record, pdu.GearMapFragment):
                boot_fragments.append(note.record.data)

        feed.add_listener(_collect_fragment)
        try:
            results = await feed.query_all()
        finally:
            feed.remove_listener(_collect_fragment)
        self._ingest_sensor_sides(results)
        message = await resync_drivetrain(feed, self.state, results, boot_fragments)
        if message is not None:
            self.log_lines.append(message)
        if self._wftnp:
            try:
                await feed.subscribe_gearing()
            except BikeError as exc:
                raise BikeSubscriptionError(
                    f"cannot subscribe to bike gearing: {exc}"
                ) from exc
        if self.allow_telemetry:
            await feed.open_telemetry(power=True, gearing=not self._wftnp)
        if self._wftnp and self._bridge is None:
            await self._start_bridge(feed)
        if self._status_task is None or self._status_task.done():
            self._status_task = asyncio.create_task(self._poll_status())

    async def _consume(self, note: Notification) -> None:
        self.state.apply(note)
        if self._bridge is not None:
            try:
                await self._bridge.handle(note)
            except BikeError as exc:
                self.log_lines.append(f"wftnp: cannot forward a frame: {exc}")
        if isinstance(note.record, pdu.ButtonFrame):
            self._on_button_frame()
        self.log_lines.append(
            f"{note.stream.value}: {note.hex} -> {type(note.record).__name__}"
        )

    async def _start_bridge(self, feed: SB20Client) -> None:
        """Serve this connection to a cycling app on the chosen port."""

        txt = {
            "serial-number": feed.identity.serial or DEFAULT_SERIAL,
            "mac-address": DEFAULT_MAC,
            "ble-service-uuids": SERVICE,
        }
        candidates: tuple[int, ...] = (self._wftnp_port, 0)
        if self._wftnp_port == 0:
            candidates = (0,)
        last_error: BaseException | None = None
        for port in candidates:
            candidate = WftnpBridge(
                feed,
                name=DEFAULT_NAME,
                txt=txt,
                port=port,
                standalone=False,
            )
            try:
                await candidate.start()
            except OSError as exc:
                last_error = exc
                self.log_lines.append(f"wftnp: port {port} unavailable: {exc}")
                continue
            except Exception as exc:
                await self._stop_bridge(candidate)
                raise WftnpStartError(
                    f"cannot start the endpoint: {exc}"
                ) from exc
            self._bridge = candidate
            self.log_lines.append(
                f"wftnp: serving {DEFAULT_NAME} on port {candidate.server.port}"
            )
            return
        raise WftnpStartError(f"cannot bind the WFTNP endpoint: {last_error}")

    async def _stop_bridge(self, bridge: WftnpBridge) -> None:
        try:
            await bridge.stop()
        except Exception as exc:
            self.log_lines.append(f"wftnp: cleanup failed: {exc}")

    async def _poll_status(self) -> None:
        assert isinstance(self.feed, SB20Client)
        while True:
            await asyncio.sleep(2.0)
            if not self._connected:
                continue
            try:
                await self.feed.get_bike_status()
            except asyncio.CancelledError:
                raise
            except BikeError as exc:
                self.log_lines.append(f"bike status poll failed: {exc}")

    @staticmethod
    def _backoff(failures: int) -> float:
        return min(RECONNECT_MAX_DELAY_S, float(2 ** min(failures - 1, 5)))

    def _event(self, message: str) -> None:
        self.log_lines.append(message)

    def _fatal(self, message: str) -> None:
        self.state.status = message
        self.log_lines.append(message)
        self.exit(return_code=1, message=message)

    def _show_reconnect_modal(self, message: str) -> None:
        if self._reconnect_modal_open:
            return
        self._reconnect_modal_open = True

        def closed(keep: bool | None) -> None:
            self._reconnect_modal_open = False
            if keep is False:
                self._fatal("bike connection unavailable; exiting")

        self.push_screen(ReconnectScreen(message), closed)

    def _ingest_sensor_sides(self, results: dict[str, Any]) -> None:
        left = results.get("sensor_firmware_left")
        right = results.get("sensor_firmware_right")
        if isinstance(left, SensorFirmware):
            self.state.ingest_sensor_firmware("left", left)
        if isinstance(right, SensorFirmware):
            self.state.ingest_sensor_firmware("right", right)

    def _refresh(self) -> None:
        self._expire_button_state()
        self.state.status = self.state.status or self.feed.status
        if self.state.mtu is None:
            self.state.mtu = self.feed.mtu
        if self.feed.identity != self.state.identity:
            merged = self.state.identity.as_dict()
            for key, value in self.feed.identity.as_dict().items():
                if value:
                    merged[key] = value
            self.state.identity = type(self.state.identity)(**merged)

        main = self._main_screen
        if main is None or not self.is_running:
            return
        try:
            main.query_one("#identity", Static).update(identity_panel(self.state, self.feed))
            main.query_one("#rider", Static).update(rider_panel(self.state))
            main.query_one("#drivetrain", Static).update(drivetrain_panel(self.state))
            status = status_panel(self.state)
            status.append("\n")
            status.append(f"{'ERG':<14}", style="dim")
            status.append(
                f"{'on' if self._erg_on else 'off'}, target {self._erg_target_w} W "
                f"(e toggle, +/- 10 W)"
            )
            main.query_one("#status", Static).update(status)
            main.query_one("#readback", Static).update(readback_panel(self.state))
            livebuttons = main.query_one("#livebuttons", Static)
            livebuttons.update(live_buttons_panel(self.state, livebuttons.size.width or main.size.width))

            log = main.query_one("#log", RichLog)
        except NoMatches:
            return
        if self._logged > len(self.log_lines):
            self._logged = 0
        new = list(self.log_lines)[self._logged:]
        for line in new:
            log.write(line)
        self._logged += len(new)

    # ------------------------------------------------------------------ actions

    def action_more_data(self) -> None:
        self.push_screen(MoreDataScreen(self.state))

    def action_options(self) -> None:
        self.push_screen(OptionsScreen(self.state, self.feed))

    def action_gearing(self) -> None:
        self.push_screen(
            GearingScreen(
                self.state,
                self.feed,
                on_simulation=self._mark_simulation,
                on_log=self.log_lines.append,
            )
        )

    def _mark_simulation(self) -> None:
        self._erg_on = False

    def action_zero_reset(self) -> None:
        self.push_screen(ZeroResetScreen(self.feed))

    async def action_toggle_erg(self) -> None:
        if not isinstance(self.feed, SB20Client):
            self.log_lines.append("ERG control unavailable in replay")
            return
        try:
            if self._erg_on:
                await self.feed.erg_off()
                self._erg_on = False
                for slot in self._button_state:
                    self._bump_button_generation(slot)
                self.log_lines.append("control: ERG off -> simulation at 0.0 % grade")
            else:
                await self.feed.erg_on(self._erg_target_w)
                self._erg_on = True
                self.log_lines.append(f"control: ERG on at {self._erg_target_w} W")
        except BikeError as exc:
            self.log_lines.append(f"ERG toggle failed: {exc}")

    async def action_erg_up(self) -> None:
        await self._set_erg_target(self._erg_target_w + 10)

    async def action_erg_down(self) -> None:
        await self._set_erg_target(self._erg_target_w - 10)

    async def _set_erg_target(self, watts: int) -> None:
        target = max(0, min(ERG_TARGET_MAX_W, watts))
        if target == self._erg_target_w:
            return
        if not isinstance(self.feed, SB20Client) or not self._erg_on:
            self._erg_target_w = target
            self.log_lines.append(f"ERG target set to {target} W (press e to start)")
            return
        try:
            await self.feed.set_target_power(target)
        except BikeError as exc:
            self.log_lines.append(f"ERG target change failed: {exc}")
            return
        self._erg_target_w = target
        self.log_lines.append(f"control: ERG target {target} W")

    def _bump_button_generation(self, slot: int) -> int:
        generation = self._button_generation.get(slot, 0) + 1
        self._button_generation[slot] = generation
        return generation

    def _current_button_state(self) -> set[int]:
        mask = self.state.pressed_mask()
        return {
            slot
            for index, slot in enumerate(pdu.BUTTON_MASK_SLOTS)
            if mask & (1 << index)
        }

    def _expire_button_state(self) -> None:
        current = self._current_button_state()
        for slot in self._button_state - current:
            self._bump_button_generation(slot)
        self._button_state = current

    def _on_button_frame(self) -> None:
        current = self._current_button_state()
        previous = self._button_state
        for slot in previous - current:
            self._bump_button_generation(slot)
        for slot in current - previous:
            generation = self._bump_button_generation(slot)
            if not self._erg_on:
                continue
            delta = self._button_erg_delta(slot)
            if delta is None:
                continue
            task = asyncio.create_task(self._hold_button(slot, delta, generation))
            self._button_tasks.add(task)
            task.add_done_callback(self._button_tasks.discard)
        self._button_state = current

    def _button_erg_delta(self, slot: int) -> int | None:
        assignments = self.state.buttons.value
        if assignments is None:
            return None
        action = assignments.by_slot.get(slot)
        if action == pdu.ButtonAction.REAR_UP:
            return 10
        if action == pdu.ButtonAction.REAR_DOWN:
            return -10
        if action == pdu.ButtonAction.FRONT_UP:
            return 20
        if action == pdu.ButtonAction.FRONT_DOWN:
            return -20
        return None

    async def _hold_button(self, slot: int, delta: int, generation: int) -> None:
        await asyncio.sleep(BUTTON_DEBOUNCE_S)
        if (
            self._button_generation.get(slot) != generation
            or slot not in self._current_button_state()
            or not self._erg_on
        ):
            return
        await self._set_erg_target(self._erg_target_w + delta)
        await asyncio.sleep(max(0.0, ERG_HOLD_DELAY_S - BUTTON_DEBOUNCE_S))
        while (
            self._button_generation.get(slot) == generation
            and slot in self._current_button_state()
            and self._erg_on
        ):
            await self._set_erg_target(self._erg_target_w + delta)
            await asyncio.sleep(ERG_HOLD_REPEAT_S)

    async def action_resend(self) -> None:
        try:
            await self.feed.query_all()
        except BikeError as exc:
            self.log_lines.append(f"re-send failed: {exc}")

    def action_clear_log(self) -> None:
        if self._main_screen is None:
            return
        self._main_screen.query_one("#log", RichLog).clear()
        self._logged = len(self.log_lines)

    async def on_unmount(self) -> None:
        errors: list[str] = []
        tasks = list(self._button_tasks)
        if self._status_task is not None:
            tasks.append(self._status_task)
            self._status_task = None
        if self._feed_task is not None:
            tasks.append(self._feed_task)
            self._feed_task = None
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                errors.append(f"task: {exc}")
        if self._bridge is not None:
            try:
                await self._bridge.stop()
            except Exception as exc:
                errors.append(f"wftnp: {exc}")
            self._bridge = None
        try:
            await self.feed.close()
        except Exception as exc:
            errors.append(f"feed close: {exc}")
        for error in getattr(self.feed, "cleanup_errors", ()):
            errors.append(f"feed cleanup: {error}")
        self._cleanup_errors.extend(errors)


# --------------------------------------------------------------------------- headless


def screen_text(app: App[Any]) -> str:
    """Render the live screen to plain text for ``--snapshot`` and tests."""

    if isinstance(app, DashApp):
        app._refresh()
    strips = app.screen._compositor.render_strips()
    return "\n".join(strip.text.rstrip() for strip in strips).rstrip()


def run_snapshot(
    feed: BikeFeed,
    state: BikeState,
    log_lines: deque[str],
    seconds: float,
    size: tuple[int, int],
    *,
    allow_telemetry: bool,
) -> str:
    async def go() -> str:
        app = DashApp(feed, state, log_lines, allow_telemetry=allow_telemetry)
        async with app.run_test(size=size) as pilot:
            await asyncio.sleep(seconds)
            await pilot.pause()
            text = screen_text(app)
        for error in app.cleanup_errors:
            print(f"cleanup: {error}", file=sys.stderr)
        return text

    return asyncio.run(go())


def summary(state: BikeState) -> dict[str, Any]:
    return state.to_dict()


def _make_feed(options: argparse.Namespace) -> BikeFeed:
    if options.replay:
        return ReplayClient(options.replay, speed=options.speed, loop=options.loop)
    return SB20Client(
        address=options.address,
        name_filter=options.name,
        scan_timeout=options.scan_timeout,
        request_timeout=options.request_timeout,
    )


def main(argv: list[str] | None = None) -> int:
    warnings.filterwarnings("ignore", message="Using default MTU value")
    parser = argparse.ArgumentParser(
        prog="openstage-tui",
        description="Stages SB20 dashboard with live ERG controls.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--replay", metavar="FILE", help="replay a JSON capture instead of BLE")
    parser.add_argument("--speed", type=float, default=1.0, help="replay speed factor")
    parser.add_argument("--loop", action="store_true", help="loop a replay")
    add_connection_arguments(parser)
    parser.add_argument("--no-telemetry", action="store_true",
                        help="do not open the reversible telemetry subscriptions")
    parser.add_argument("--no-wftnp", action="store_true",
                        help="do not serve the WFTNP endpoint to a local app")
    parser.add_argument("--debug", action="store_true",
                        help="write DEBUG logging, including WFTNP frames, to openstage-tui.log")
    parser.add_argument("--snapshot", type=float, metavar="SECONDS",
                        help="headless: feed for SECONDS, print the screen, exit")
    parser.add_argument("--json", action="store_true",
                        help="with --snapshot, also dump state as JSON")
    parser.add_argument("--width", type=int, default=110)
    parser.add_argument("--height", type=int, default=36)
    options = parser.parse_args(argv)

    if options.debug:
        logging.basicConfig(
            filename="openstage-tui.log",
            filemode="w",
            level=logging.DEBUG,
            format="%(asctime)s.%(msecs)03d %(name)s %(levelname)s %(message)s",
            datefmt="%H:%M:%S",
        )

    wftnp_port: int | None = None
    if options.snapshot is None and options.replay is None and not options.no_wftnp:
        try:
            wftnp_port = asyncio.run(preflight_port(DEFAULT_PORT))
        except Exception as exc:
            print(
                f"openstage-tui: WFTNP preflight failed: {exc}",
                file=sys.stderr,
            )
            return 1

    try:
        feed = _make_feed(options)
    except CaptureFormatError as exc:
        print(f"openstage-tui: {exc}", file=sys.stderr)
        return 1
    state = BikeState()
    log_lines: deque[str] = deque(maxlen=400)
    allow_telemetry = not options.no_telemetry

    if options.snapshot is not None:
        text = run_snapshot(
            feed,
            state,
            log_lines,
            options.snapshot,
            (options.width, options.height),
            allow_telemetry=allow_telemetry,
        )
        print(text)
        print(f"\n[{state.status or feed.status}]")
        if options.json:
            print(json.dumps(summary(state), indent=1))
        return 0

    app = DashApp(
        feed,
        state,
        log_lines,
        allow_telemetry=allow_telemetry,
        wftnp=not options.no_wftnp,
        wftnp_port=wftnp_port,
    )
    try:
        app.run()
    except KeyboardInterrupt:
        return 130
    finally:
        for error in app.cleanup_errors:
            print(f"cleanup: {error}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
