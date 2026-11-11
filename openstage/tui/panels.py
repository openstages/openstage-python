"""Pure Textual/rich panel builders.

These functions take :class:`openstage.state.BikeState` and return
``rich.text.Text``; they contain no Textual widgets and are unit-testable
without a terminal.
"""

from __future__ import annotations

from typing import Any

from rich.text import Text

from .. import pdu
from ..client import BikeFeed
from ..state import BikeState, Reading

# The physical input service reports 14 slots.  Slot 19 exists only as the
# 15th value on the API's 0x0B wire format and has no button-active bit.


def fmt(value: object, unit: str = "", nd: int = 1) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return f"{value:.{nd}f}{unit}"
    if hasattr(value, "name") and not isinstance(value, str):
        name = getattr(value, "name")
        if isinstance(name, str):
            return name.lower().replace("_", " ")
    return f"{value}{unit}"


def _val(reading: Reading[Any], unit: str = "", nd: int = 1) -> str:
    return fmt(reading.value, unit, nd)


def _src(reading: Reading[Any]) -> str:
    return f"({reading.source})" if reading.value is not None and reading.source else ""


def _fields(pairs: list[tuple[str, str]], width: int = 14) -> Text:
    text = Text()
    for index, (label, value) in enumerate(pairs):
        if index:
            text.append("\n")
        text.append(f"{label:<{width}}", style="dim")
        text.append(value)
    return text


def rider_panel(state: BikeState) -> Text:
    balance = state.balance_pct.value
    text = _fields(
        [
            ("power", f"{_val(state.power_w, ' W'):>12}  {_src(state.power_w)}"),
            ("cadence", f"{_val(state.cadence_rpm, ' rpm'):>12}  {_src(state.cadence_rpm)}"),
            ("speed", f"{_val(state.speed_kph, ' km/h', 2):>12}  {_src(state.speed_kph)}"),
            ("distance", f"{_distance(state.distance_m.value):>12}"),
            ("L/R dist", f"{('—' if balance is None else balance):>12}  "
                         f"UI 100−x {('—' if balance is None else 100 - balance)}   "
                         f"diff {_val(state.lr_difference)}"),
        ]
    )
    if state.power_w.value is None and state.cadence_rpm.value is None:
        text.append("\n")
        text.append("(bike stationary/standby — power and cadence appear once you ride)",
                    style="dim")
    return text


def drivetrain_panel(state: BikeState) -> Text:
    view = state.drivetrain_view()
    text = Text()
    if view.model is None and not view.teeth_rear and not view.coefficients:
        text.append("waiting for the drivetrain model (0x0C/2/7)…", style="dim")
        return text

    text.append(f"{'model':<14}", style="dim")
    text.append(f"{fmt(view.model)}\n")
    text.append(f"{'declared':<14}", style="dim")
    text.append(f"{view.front_count or '?'} front × {view.rear_count or '?'} rear")
    if view.map_size:
        text.append(f"   map {view.map_bytes}/{view.map_size} B")
    text.append("\n")
    text.append(f"{'crc8':<14}", style="dim")
    text.append(f"0x{view.crc8:02X}" if view.crc8 is not None else "—")
    text.append(f"   step {view.step}   "
                f"dream-drive {'yes' if view.dream_drive else 'no'}\n")

    gear = state.gear.value
    if gear is not None and view.model is not None and gear.model is not view.model:
        gear = None
    if gear is not None and (
        (view.front_count is not None and not 1 <= gear.front_index <= view.front_count)
        or (view.rear_count is not None and not 1 <= gear.rear_index <= view.rear_count)
    ):
        # Notification from a different active map (e.g. captured before the
        # boot flash read reset it); never render an impossible index.
        gear = None
    stale = gear is None and state.gear.value is not None
    current_front = gear.front_index if gear is not None else None
    current_rear = gear.rear_index if gear is not None else None
    front_count = view.front_count or 0
    rear_count = view.rear_count or 0

    if view.model is pdu.ModelType.COEFFICIENTS:
        text.append(f"{'coef':<14}", style="dim")
        for index in range(1, rear_count + 1):
            coefficient = view.coefficients.get(index)
            if (
                coefficient is None
                and gear is not None
                and index == current_rear
                and gear.coefficient is not None
            ):
                coefficient = gear.coefficient
            text.append(f"{coefficient if coefficient is not None else '·'} ",
                        style="bold reverse" if index == current_rear else "")
        text.append("\n")
        known = len(view.coefficients)
        if rear_count and known < rear_count:
            text.append(
                f"(map {view.map_bytes}/{view.map_size} B; other coefficients "
                f"appear as you shift)",
                style="dim",
            )
        if gear is not None and gear.coefficient is not None:
            text.append(
                f"\ncurrent: coef {gear.coefficient} (gear {gear.rear_index})\n"
            )
        if stale:
            text.append("\ncurrent: stale for this header; shift to refresh\n",
                        style="dim")
        return text

    text.append(f"{'front':<14}", style="dim")
    for index in range(1, front_count + 1):
        teeth = view.teeth_front.get(index)
        if not teeth and gear is not None and index == current_front:
            teeth = gear.front_teeth
        text.append(f"{teeth if teeth else '·'}T ",
                    style="bold reverse" if index == current_front else "")
    text.append("\n")
    text.append(f"{'rear':<14}", style="dim")
    for index in range(1, rear_count + 1):
        teeth = view.teeth_rear.get(index)
        if not teeth and gear is not None and index == current_rear:
            teeth = gear.rear_teeth
        text.append(f"{teeth if teeth else '·'} ",
                    style="bold reverse" if index == current_rear else "")
    text.append("\n")
    known = len(view.teeth_rear)
    if rear_count and known < rear_count:
        text.append(
            f"(map {view.map_bytes}/{view.map_size} B; other cogs appear as "
            f"you shift)",
            style="dim",
        )
    if gear is not None:
        text.append(
            f"\ncurrent: {gear.front_teeth}T / {gear.rear_teeth}T "
            f"(front {gear.front_index}, rear {gear.rear_index})\n"
        )
    if stale:
        text.append("\ncurrent: stale for this header; shift to refresh\n",
                    style="dim")
    return text


def status_panel(state: BikeState) -> Text:
    return _fields(
        [
            ("flywheel", _val(state.flywheel_rpm, " rpm")),
            ("HRM", fmt(state.hrm.value)),
            ("power meter", fmt(state.power_meter.value)),
            ("pairing", fmt(state.pairing_status.value)),
            ("brake L/R", f"{_val(state.brake_left)} / {_val(state.brake_right)}"),
        ]
    )


def readback_panel(state: BikeState) -> Text:
    resistance = state.resistance.value
    scaling = state.gradient_scaling.value
    weight = state.weight.value
    parameters = state.parameters.value
    pairing = state.pairing.value
    battery = state.battery.value
    left = state.sensor_firmware_left.value
    right = state.sensor_firmware_right.value
    battery_percent = None if battery is None else battery.percent
    return _fields(
        [
            ("resistance", f"{resistance.level} ({resistance.percent:.1f}%)"
             if resistance else "—"),
            ("grade scale", f"{scaling.percent:g}%" if scaling else "—"),
            ("weight", f"{weight.user_kg:.2f} / {weight.equipment_kg:.2f} kg"
             if weight else "—"),
            ("brake", fmt(None if parameters is None else parameters.brake_enabled)),
            ("dual-BLE", fmt(None if parameters is None else parameters.dual_ble_enabled)),
            ("crank", f"{pairing.crank_length_mm:.1f} mm"
             if pairing and pairing.crank_length_mm is not None else "—"),
            ("PM battery", f"{battery_percent}%" if battery_percent is not None else "—"),
            ("sensor fw L", left.version if left and left.version else "—"),
            ("sensor fw R", right.version if right and right.version else "—"),
        ]
    )


def buttons_panel(state: BikeState) -> Text:
    assignments = state.buttons.value
    text = Text()
    if assignments is None:
        text.append("waiting for the button configuration (0x0B/1)…", style="dim")
        return text
    text.append(f"{'wire':<8}", style="dim")
    text.append(" ".join(f"{value:02X}" for value in assignments.values))
    text.append("\n")
    text.append(f"{'buttons':<8}", style="dim")
    text.append(", ".join(str(index) for index in range(1, len(pdu.BUTTON_MASK_SLOTS) + 1)))
    text.append("   (physical order; wire slots 1,2,3,6,7,8,11…18)", style="dim")
    text.append("\n")
    entries = [
        (index, assignments.by_slot.get(slot, 0))
        for index, slot in enumerate(pdu.BUTTON_MASK_SLOTS, start=1)
    ]
    rows = 5
    for row in range(rows):
        cells = []
        for column in range(3):
            index = column * rows + row
            if index < len(entries):
                physical, value = entries[index]
                cells.append(f"{physical:>2}:{value:>2} {BUTTON_NAMES.get(value, '?'):<17}")
        text.append(" ".join(cells).rstrip() + "\n")
    return text


def live_buttons_panel(state: BikeState, width: int = 80) -> Text:
    mask = state.pressed_mask()
    text = Text()
    per_row = max(1, (max(width, 20) - 4) // 7)
    slots = pdu.BUTTON_MASK_SLOTS
    for index, _slot in enumerate(slots):
        pressed = bool(mask & (1 << index))
        text.append(f"{index + 1:>2} ", style="bold" if pressed else "dim")
        text.append("●" if pressed else "○", style="bold green" if pressed else "dim")
        text.append("   ")
        if index % per_row == per_row - 1 and index != len(slots) - 1:
            text.append("\n")
    text.append("\n")
    if not state.counts.get(pdu.Stream.BUTTONS):
        text.append("no button frames received", style="dim")
    else:
        text.append(
            f"{state.button_events} events   mask 0x{mask:04X}",
            style="dim",
        )
    return text


def frames_panel(state: BikeState) -> Text:
    text = Text()
    if not state.counts:
        text.append("(no frames yet)", style="dim")
        return text
    for stream in sorted(state.counts, key=lambda item: item.value):
        text.append(f"{stream.value:<12}", style="dim")
        text.append(f"{state.counts[stream]}\n")
    text.append(f"{'total':<12}", style="dim")
    text.append(str(sum(state.counts.values())))
    return text


def identity_panel(state: BikeState, feed: BikeFeed) -> Text:
    identity = state.identity.as_dict()
    text = Text()
    text.append(f"{'device':<14}", style="dim")
    text.append(f"{identity.get('name') or '?'}   ")
    text.append(f"{identity.get('address') or '—'}   ")
    text.append(f"MTU {state.mtu if state.mtu is not None else '—'}   ")
    text.append(f"{feed.mode}\n")
    text.append(f"{'identity':<14}", style="dim")
    text.append(
        f"serial {identity.get('serial') or '—'}   "
        f"sw {identity.get('software') or '—'}   "
        f"fw {identity.get('firmware') or '—'}   "
        f"hw {identity.get('hardware') or '—'}   "
        f"mfr {identity.get('manufacturer') or '—'}\n"
    )
    text.append(f"{'state':<14}", style="dim")
    text.append(state.status or feed.status)
    return text


BUTTON_NAMES = {
    0: "undefined",
    1: "chainring +",
    2: "chainring -",
    3: "cassette +",
    4: "cassette -",
    5: "external",
    6: "n/a",
    7: "SRAM +",
    8: "SRAM -",
    9: "SRAM front toggle",
    10: "navigate left",
    11: "navigate right",
    12: "navigate up",
    13: "navigate down",
    14: "select",
    15: "back",
    16: "power up",
    17: "ride on",
    18: "steer left",
    19: "steer right",
    20: "cassette + multi",
    21: "cassette - multi",
    22: "SRAM + multi",
    23: "SRAM - multi",
    24: "HUD toggle",
    25: "map toggle",
    26: "skip interval",
    27: "pause",
    28: "resume",
    29: "lap",
    30: "previous interval",
    31: "menu",
    32: "home",
    33: "push to talk",
    34: "U-turn",
    35: "toggle mode",
    36: "take a break",
    37: "join another rider",
    38: "change route",
    39: "camera view",
    40: "screenshot",
    41: "cruise control",
    42: "workout harder",
    43: "workout easier",
}


def _distance(value: int | None) -> str:
    if value is None:
        return "—"
    return f"{value / 1000:.3f} km" if value >= 1000 else f"{value} m"
