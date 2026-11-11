"""Headless read-only probe.

The probe connects to the bike, runs the allow-listed queries, optionally
records telemetry, and prints a summary. It never writes a configuration
value. It can also replay a saved capture without a radio.

Examples::

    openstage-probe --address AA:BB:CC:DD:EE:FF --capture session.json
    openstage-probe --seconds 60 --capture ride.json
    openstage-probe --replay session.json --json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from contextlib import aclosing
from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path
from typing import Any

from . import pdu
from .cli import add_connection_arguments
from .capture import Capture, CapturedExchange, CapturedFrame, load_capture
from .client import BikeError, BikeFeed, SB20Client
from .replay import ReplayClient
from .state import BikeState


@dataclass(slots=True)
class ProbeOptions:
    address: str | None = None
    name: str | None = None
    scan_timeout: float = 5.0
    request_timeout: float = 5.0
    seconds: float = 0.0
    telemetry: bool = True
    capture: str | None = None
    as_json: bool = False
    buttons: bool = True
    ftms: bool = True
    csc: bool = True
    replay: str | None = None
    replay_speed: float = 10.0
    loop: bool = False


async def _consume(
    feed: BikeFeed,
    state: BikeState,
    seconds: float,
    frames: list[CapturedFrame],
    started: float,
) -> None:
    deadline = None if seconds <= 0 else time.monotonic() + seconds
    async with aclosing(feed.notifications()) as iterator:
        while True:
            remaining = None if deadline is None else deadline - time.monotonic()
            if remaining is not None and remaining <= 0:
                return
            try:
                if remaining is None:
                    note = await anext(iterator)
                else:
                    note = await asyncio.wait_for(anext(iterator), timeout=remaining)
            except (TimeoutError, StopAsyncIteration):
                return
            state.apply(note)
            frames.append(
                CapturedFrame(t=round(note.t - started, 3), stream=note.stream, raw=note.raw)
            )


async def _run_live(options: ProbeOptions) -> int:
    state = BikeState()
    frames: list[CapturedFrame] = []
    exchanges: list[CapturedExchange] = []

    client = SB20Client(
        address=options.address,
        name_filter=options.name,
        scan_timeout=options.scan_timeout,
        request_timeout=options.request_timeout,
        subscribe_buttons=options.buttons,
        subscribe_ftms=options.ftms,
        subscribe_csc=options.csc,
    )
    started = time.monotonic()
    exit_code = 0
    final_status = "not connected"
    try:
        print("probe: scanning/connecting…", file=sys.stderr, flush=True)
        await client.connect()
        state.identity = client.identity
        state.mtu = client.mtu
        state.status = client.status
        print(f"probe: connected to {client.identity.name or client.identity.address}",
              file=sys.stderr, flush=True)

        results = await client.query_all()
        print(f"probe: queries complete ({len(results)})", file=sys.stderr, flush=True)
        for request in pdu.QUERY_REQUESTS:
            result = results.get(request.name)
            if isinstance(result, pdu.Pdu):
                exchanges.append(CapturedExchange(request=request.pdu,
                                                  responses=(result.raw,)))

        if options.telemetry and options.seconds > 0:
            await client.open_telemetry(power=True, gearing=True)
            print(f"probe: telemetry open; recording for {options.seconds:g}s",
                  file=sys.stderr, flush=True)
            await _consume(client, state, options.seconds, frames, started)
            print("probe: recording window closed", file=sys.stderr, flush=True)
        else:
            # Query responses are queued by the client and folded by _consume;
            # this short drain also catches late drivetrain fragments.
            await _consume(client, state, 0.25, frames, started)
        # The firmware response does not identify the side, so tag the two
        # explicit sensor requests after their queued frames have been applied.
        left = results.get(pdu.SENSOR_FIRMWARE_LEFT.name)
        right = results.get(pdu.SENSOR_FIRMWARE_RIGHT.name)
        if isinstance(left, pdu.SensorFirmware):
            state.ingest_sensor_firmware("left", left)
        if isinstance(right, pdu.SensorFirmware):
            state.ingest_sensor_firmware("right", right)
        final_status = client.status
    except BikeError as exc:
        final_status = f"error: {exc}"
        print(f"probe error: {exc}", file=sys.stderr)
        exit_code = 1
    finally:
        await client.close()

    state.status = final_status
    _report(options, state, client.identity, client.mtu, frames, exchanges, client.notes)
    return exit_code


async def _run_replay(options: ProbeOptions) -> int:
    assert options.replay is not None
    state = BikeState()
    frames: list[CapturedFrame] = []
    client = ReplayClient(options.replay, speed=options.replay_speed, loop=options.loop)
    await client.connect()
    state.identity = client.identity
    state.mtu = client.mtu
    await _consume(client, state, options.seconds, frames, time.monotonic())
    await client.close()
    state.status = client.status
    capture = load_capture(options.replay)
    _report(options, state, client.identity, client.mtu, frames, list(capture.exchanges),
            capture.notes)
    return 0


def _report(
    options: ProbeOptions,
    state: BikeState,
    identity: Any,
    mtu: int | None,
    frames: list[CapturedFrame],
    exchanges: list[CapturedExchange],
    notes: tuple[str, ...],
) -> None:
    if options.capture:
        capture = Capture(
            identity=identity,
            mtu=mtu,
            frames=tuple(frames),
            exchanges=tuple(exchanges),
            notes=notes,
        )
        capture.write(options.capture)

    summary = state.to_dict()
    summary["notes"] = list(notes)
    summary["frames"] = len(frames)
    summary["exchanges"] = len(exchanges)
    if options.capture:
        summary["capture"] = options.capture

    if options.as_json:
        print(json.dumps(summary, indent=1, sort_keys=True))
        return

    print(f"device: {identity.name or '?'}  {identity.address or '?'}")
    print(f"mtu:    {mtu if mtu is not None else '—'}")
    print(f"state:  {state.status}")
    print(f"frames: {len(frames)}   exchanges: {len(exchanges)}")
    if identity.serial:
        print(
            "DIS:    "
            f"serial {identity.serial}  software {identity.software}  "
            f"firmware {identity.firmware}  hardware {identity.hardware}  "
            f"manufacturer {identity.manufacturer}"
        )
    def display(value: Any) -> str:
        if value is None:
            return "—"
        if isinstance(value, pdu.SensorFirmware):
            return value.version or f"command {value.command}"
        if isinstance(value, pdu.PowerMeterPairing):
            return f"crank {value.crank_length_mm} mm"
        if isinstance(value, pdu.Resistance):
            return f"{value.level} ({value.percent:.1f}%)"
        if isinstance(value, pdu.GradientScaling):
            return f"{value.percent:g}%"
        if isinstance(value, pdu.Weight):
            return f"{value.user_kg:.2f} / {value.equipment_kg:.2f} kg"
        if isinstance(value, pdu.Parameters):
            return f"brake {value.brake_enabled}  dual-BLE {value.dual_ble_enabled}"
        if isinstance(value, pdu.Battery):
            return f"{value.percent}%"
        if isinstance(value, pdu.ButtonAssignments):
            return " ".join(f"{item:02X}" for item in value.values)
        if isinstance(value, pdu.GearNotification):
            if value.model is pdu.ModelType.COEFFICIENTS:
                return f"rear {value.rear_index} coefficient {value.coefficient}"
            return f"front {value.front_index}/{value.front_teeth}T  rear {value.rear_index}/{value.rear_teeth}T"
        if isinstance(value, IntEnum):
            return value.name.lower()
        return str(value)

    def show(label: str, reading: Any) -> None:
        suffix = f"  ({reading.source})" if reading.value is not None and reading.source else ""
        print(f"{label:<18}{display(reading.value)}{suffix}")

    show("power", state.power_w)
    show("cadence", state.cadence_rpm)
    show("balance", state.balance_pct)
    show("flywheel rpm", state.flywheel_rpm)
    show("HRM", state.hrm)
    show("power meter", state.power_meter)
    show("pairing", state.pairing_status)
    show("resistance", state.resistance)
    show("gradient scale", state.gradient_scaling)
    show("weight", state.weight)
    show("parameters", state.parameters)
    show("crank pairing", state.pairing)
    show("battery", state.battery)
    show("buttons", state.buttons)
    show("sensor fw left", state.sensor_firmware_left)
    show("sensor fw right", state.sensor_firmware_right)
    show("gear", state.gear)
    view = state.drivetrain_view()
    model_name = view.model.name.lower() if view.model is not None else "?"
    extra = ""
    if view.model is pdu.ModelType.COEFFICIENTS:
        extra = f"  coefficients {len(view.coefficients)}"
    print(f"{'drivetrain':<18}{model_name}  {view.front_count} front × "
          f"{view.rear_count} rear  map {view.map_bytes}/{view.map_size} B{extra}")
    if state.last_error is not None:
        print(f"last error: {state.last_error.raw.hex()}")
    for note in notes[-5:]:
        print(f"note: {note}")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="openstage-probe",
        description="Read-only Stages SB20 probe (queries and reversible subscriptions only).",
    )
    add_connection_arguments(parser)
    parser.add_argument("--seconds", type=float, default=0.0,
                        help="telemetry capture duration; 0 means queries only")
    parser.add_argument("--capture", metavar="FILE", help="write the session as JSON")
    parser.add_argument("--json", action="store_true", dest="as_json",
                        help="emit the summary as JSON")
    parser.add_argument("--no-buttons", action="store_false", dest="buttons",
                        help="do not subscribe to the button/brake service")
    parser.add_argument("--no-ftms", action="store_false", dest="ftms",
                        help="do not subscribe to FTMS Indoor Bike Data")
    parser.add_argument("--no-csc", action="store_false", dest="csc",
                        help="do not subscribe to CSC")
    parser.add_argument("--replay", metavar="FILE", help="replay a capture instead of using BLE")
    parser.add_argument("--replay-speed", type=float, default=10.0)
    parser.add_argument("--loop", action="store_true", help="loop a replay")
    return parser


def main(argv: list[str] | None = None) -> int:
    namespace = _build_parser().parse_args(argv)
    options = ProbeOptions(
        address=namespace.address,
        name=namespace.name,
        scan_timeout=namespace.scan_timeout,
        request_timeout=namespace.request_timeout,
        seconds=namespace.seconds,
        capture=namespace.capture,
        as_json=namespace.as_json,
        buttons=namespace.buttons,
        ftms=namespace.ftms,
        csc=namespace.csc,
        replay=namespace.replay,
        replay_speed=namespace.replay_speed,
        loop=namespace.loop,
    )
    try:
        if options.replay:
            return asyncio.run(_run_replay(options))
        return asyncio.run(_run_live(options))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
