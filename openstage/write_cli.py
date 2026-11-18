"""Command-line configuration writes.

The CLI reads the current value, sends one explicit Set, reads it back, and
optionally stores the settings record in flash. Every live write requires
``--yes``. Drivetrain changes are available in the library and the dashboard,
not here.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Awaitable
from typing import Any

from . import pdu
from .cli import add_connection_arguments
from .client import (
    BikeError,
    SB20Client,
    SettingsCommitError,
    StoreResult,
    WriteResult,
)


async def _safe_read(operation: Awaitable[Any]) -> Any:
    try:
        return await operation
    except BikeError as exc:
        return exc


def _pdu(value: pdu.Pdu) -> dict[str, str]:
    return {"type": type(value).__name__, "hex": value.raw.hex()}


def _store(value: StoreResult) -> dict[str, object]:
    return {
        "stored": value.stored,
        "busy": value.busy,
        "command": None if value.command is None else value.command.name,
        "messages": [_pdu(message) for message in value.messages],
    }


def _write(value: WriteResult) -> dict[str, object]:
    return {
        "set": _pdu(value.set_response),
        "persisted": value.persisted,
        "store": None if value.store is None else _store(value.store),
    }


def _parse_tristate(value: str) -> bool | None:
    if value == "true":
        return True
    if value == "false":
        return False
    return None


def _parse_actions(text: str) -> tuple[pdu.ButtonAction, ...]:
    parts = text.replace(",", " ").split()
    if len(parts) != len(pdu.BUTTON_MASK_SLOTS):
        raise ValueError(
            f"expected {len(pdu.BUTTON_MASK_SLOTS)} actions in slot order, got {len(parts)}"
        )
    return tuple(pdu.parse_button_action(part) for part in parts)


def _parse_assignments(text: str) -> dict[int, pdu.ButtonAction]:
    changes: dict[int, pdu.ButtonAction] = {}
    for item in text.replace(",", " ").split():
        if "=" not in item:
            raise ValueError(f"expected button=action, got {item!r}")
        slot_text, action_text = item.split("=", 1)
        changes[int(slot_text, 0)] = pdu.parse_button_action(action_text)
    if not changes:
        raise ValueError("no button assignments supplied")
    return changes


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="openstage-write",
        description="Explicit configuration writes for the Stages SB20. "
                    "Live writes require --yes.",
    )
    common = argparse.ArgumentParser(add_help=False)
    add_connection_arguments(common)
    common.add_argument("--store-timeout", type=float, default=10.0)
    common.add_argument("--zero-timeout", type=float, default=30.0)
    common.add_argument("--json", action="store_true", dest="as_json")
    common.add_argument("--yes", action="store_true",
                        help="confirm that this is an approved live write")

    sub = parser.add_subparsers(dest="command", required=True)
    weight = sub.add_parser("weight", parents=[common],
                            help="set user and/or equipment weight")
    weight.add_argument("--user-kg", type=float)
    weight.add_argument("--equipment-kg", type=float)
    weight.add_argument("--store", action="store_true",
                        help="send 0xFD/0 after the Set")

    parameters = sub.add_parser("parameters", parents=[common],
                                help="set brake/dual-BLE parameters")
    parameters.add_argument("--brake", choices=("true", "false", "unchanged"))
    parameters.add_argument("--dual", choices=("true", "false", "unchanged"))
    parameters.add_argument("--store", action="store_true")

    buttons = sub.add_parser("buttons", parents=[common],
                             help="set buttons by semantic action")
    buttons.add_argument("--actions",
                         help="14 actions in slot order: rear_up, rear_down, "
                              "front_up, front_down, not_assigned")
    buttons.add_argument("--assign",
                         help="physical button=action pairs, e.g. "
                              "1=front_up,2=front_down,4=rear_up,5=rear_down")
    buttons.add_argument("--store", action="store_true")

    gradient = sub.add_parser("gradient", parents=[common],
                              help="set gradient scaling factor")
    gradient_group = gradient.add_mutually_exclusive_group(required=True)
    gradient_group.add_argument("--factor", type=int)
    gradient_group.add_argument("--percent", type=float)
    gradient.add_argument("--delay", action="store_true")
    gradient.add_argument("--store", action="store_true")

    resistance = sub.add_parser("resistance", parents=[common],
                                help="set resistance level")
    resistance_group = resistance.add_mutually_exclusive_group(required=True)
    resistance_group.add_argument("--level", type=int, help="raw 0..255")
    resistance_group.add_argument("--percent", type=float, help="0..100%%")

    crank = sub.add_parser("crank-length", parents=[common],
                           help="set crank length and restart PM pairing")
    crank.add_argument("--mm", type=float, required=True,
                       choices=pdu.CRANK_LENGTHS_MM)
    crank.add_argument("--store", action="store_true")

    zero = sub.add_parser("zero-reset", parents=[common],
                          help="initiate zero reset and print calibration status")
    zero.set_defaults(store=False)

    power = sub.add_parser("power", parents=[common],
                           help="set ERG target power")
    power.add_argument("--watts", type=float, required=True)
    power.set_defaults(store=False)

    grade = sub.add_parser("grade", parents=[common],
                           help="set simulation grade")
    grade.add_argument("--percent", type=float, required=True)
    grade.set_defaults(store=False)

    erg = sub.add_parser("erg", parents=[common],
                         help="ERG on at target watts, or off (simulation at 0 %% grade)")
    erg_group = erg.add_mutually_exclusive_group(required=True)
    erg_group.add_argument("--watts", type=float)
    erg_group.add_argument("--off", action="store_true")
    erg.set_defaults(store=False)

    store = sub.add_parser("store", parents=[common],
                           help="send 0xFD/0 and wait for FD 03/04")
    store.set_defaults(store=True)

    return parser


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    client = SB20Client(
        address=args.address,
        name_filter=args.name,
        scan_timeout=args.scan_timeout,
        request_timeout=args.request_timeout,
        store_timeout=args.store_timeout,
    )
    out: dict[str, Any] = {
        "command": args.command,
        "device": None,
        "baseline": None,
        "write": None,
        "readback": None,
    }
    try:
        await client.connect()
        out["device"] = {"name": client.identity.name, "address": client.identity.address}

        if args.command == "weight":
            current_weight = await client.get_weight()
            out["baseline"] = {"user_kg": current_weight.user_kg,
                               "equipment_kg": current_weight.equipment_kg}
            user = current_weight.user_kg if args.user_kg is None else args.user_kg
            equipment = (current_weight.equipment_kg if args.equipment_kg is None
                         else args.equipment_kg)
            weight_result = await client.set_weight(user, equipment, store=args.store)
            out["write"] = _write(weight_result)
            weight_readback = await _safe_read(client.get_weight())
            if isinstance(weight_readback, Exception):
                out["readback"] = {"error": str(weight_readback)}
                out["state"] = "unknown: the write may have been applied"
            else:
                out["readback"] = {"user_kg": weight_readback.user_kg,
                                   "equipment_kg": weight_readback.equipment_kg}
        elif args.command == "parameters":
            current_parameters = await client.get_parameters()
            out["baseline"] = {"brake_raw": current_parameters.brake_raw,
                               "dual_ble_raw": current_parameters.dual_ble_raw}
            brake = _parse_tristate(args.brake) if args.brake else None
            dual = _parse_tristate(args.dual) if args.dual else None
            parameters_result = await client.set_parameters(brake, dual, store=args.store)
            out["write"] = _write(parameters_result)
            parameters_readback = await _safe_read(client.get_parameters())
            if isinstance(parameters_readback, Exception):
                out["readback"] = {"error": str(parameters_readback)}
                out["state"] = "unknown: the write may have been applied"
            else:
                out["readback"] = {"brake_raw": parameters_readback.brake_raw,
                                   "dual_ble_raw": parameters_readback.dual_ble_raw}
        elif args.command == "gradient":
            current_gradient = await client.get_gradient_scaling()
            out["baseline"] = {"factor": current_gradient.factor,
                               "percent": current_gradient.percent,
                               "delay_enabled": current_gradient.delay_enabled}
            factor = args.factor if args.factor is not None else round(args.percent * 2)
            gradient_result = await client.set_gradient_scaling(
                factor, args.delay, store=args.store
            )
            out["write"] = _write(gradient_result)
            gradient_readback = await _safe_read(client.get_gradient_scaling())
            if isinstance(gradient_readback, Exception):
                out["readback"] = {"error": str(gradient_readback)}
                out["state"] = "unknown: the write may have been applied"
            else:
                out["readback"] = {"factor": gradient_readback.factor,
                                   "percent": gradient_readback.percent,
                                   "delay_enabled": gradient_readback.delay_enabled}
        elif args.command == "resistance":
            current_resistance = await client.get_resistance()
            out["baseline"] = {"level": current_resistance.level,
                               "percent": current_resistance.percent}
            level = (args.level if args.level is not None
                     else round(args.percent * 2.55))
            percent = args.percent if args.level is None else level / 2.55
            resistance_result = await client.set_target_resistance(percent)
            out["write"] = _write(resistance_result)
            resistance_readback = await _safe_read(client.get_resistance())
            if isinstance(resistance_readback, Exception):
                out["readback"] = {"error": str(resistance_readback)}
                out["state"] = "unknown: the write may have been applied"
            else:
                out["readback"] = {"level": resistance_readback.level,
                                   "percent": resistance_readback.percent}
        elif args.command == "crank-length":
            current_pairing = await client.get_pairing()
            out["baseline"] = _pdu(current_pairing)
            crank_result = await client.set_crank_length(args.mm, store=args.store)
            out["write"] = _write(crank_result)
            crank_readback = await _safe_read(client.get_pairing())
            if isinstance(crank_readback, Exception):
                out["readback"] = {"error": str(crank_readback)}
                out["state"] = "unknown: the write may have been applied"
            else:
                out["readback"] = _pdu(crank_readback)
        elif args.command == "zero-reset":
            zero = await client.zero_reset()
            out["write"] = {"zero_reset": _pdu(zero)}
            out["readback"] = {
                "status": zero.status.name,
                "successful": zero.successful,
                "valid_mask": zero.valid_mask,
                "left_offset": zero.left_offset,
                "right_offset": zero.right_offset,
                "manufacturer_code": zero.manufacturer_code,
                "company_id": zero.company_id,
                "torque_offset": zero.torque_offset,
            }
        elif args.command == "power":
            power_result = await client.set_target_power(args.watts)
            out["write"] = _write(power_result)
        elif args.command == "grade":
            grade_result = await client.set_target_grade(args.percent)
            out["write"] = _write(grade_result)
        elif args.command == "erg":
            if args.off:
                erg_result = await client.erg_off()
            else:
                erg_result = await client.erg_on(args.watts)
            out["write"] = _write(erg_result)
        elif args.command == "buttons":
            current_buttons = await client.get_buttons()
            out["baseline"] = {
                "wire": list(current_buttons.values),
                "actions": [
                    None if action is None else action.name
                    for action in pdu.button_actions_from_wire(current_buttons.values)
                ],
            }
            if args.actions:
                actions = _parse_actions(args.actions)
                buttons_result = await client.set_button_actions(actions, store=args.store)
            else:
                assert args.assign is not None
                changes = _parse_assignments(args.assign)
                buttons_result = await client.set_button_slots(changes, store=args.store)
            out["write"] = _write(buttons_result)
            buttons_readback = await _safe_read(client.get_buttons())
            if isinstance(buttons_readback, Exception):
                out["readback"] = {"error": str(buttons_readback)}
                out["state"] = "unknown: the write may have been applied"
            else:
                out["readback"] = {
                    "wire": list(buttons_readback.values),
                    "actions": [
                        None if action is None else action.name
                        for action in pdu.button_actions_from_wire(
                            buttons_readback.values
                        )
                    ],
                }
        else:  # store
            store_result = await client.store_settings()
            out["write"] = {"store": _store(store_result)}
    finally:
        await client.close()
    return out


def _print_human(out: dict[str, Any]) -> None:
    device = out.get("device") or {}
    print(f"device: {device.get('name')} {device.get('address')}")
    if out.get("baseline") is not None:
        print(f"baseline: {out['baseline']}")
    write = out.get("write")
    if write:
        print(f"write: {write}")
    if out.get("readback") is not None:
        print(f"readback: {out['readback']}")


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if not args.yes:
        print("refusing live write without --yes", file=sys.stderr)
        return 2
    if args.command == "weight" and args.user_kg is None and args.equipment_kg is None:
        print("weight requires --user-kg and/or --equipment-kg", file=sys.stderr)
        return 2
    if args.command == "parameters" and args.brake is None and args.dual is None:
        print("parameters requires --brake and/or --dual", file=sys.stderr)
        return 2
    if args.command == "buttons" and ((args.actions is None) == (args.assign is None)):
        print("buttons requires exactly one of --actions or --assign", file=sys.stderr)
        return 2
    try:
        out = asyncio.run(_run(args))
    except KeyboardInterrupt:
        return 130
    except SettingsCommitError as exc:
        print(f"write failed at {exc.step}: {exc}", file=sys.stderr)
        print("device state is unknown; verify it before retrying", file=sys.stderr)
        return 1
    except (BikeError, ValueError) as exc:
        print(f"write failed: {exc}", file=sys.stderr)
        return 1
    readback = out.get("readback")
    if isinstance(readback, dict) and "error" in readback:
        if args.as_json:
            print(json.dumps(out, indent=1, sort_keys=True))
        else:
            _print_human(out)
        print(f"readback failed: {readback['error']}", file=sys.stderr)
        print(f"state: {out.get('state', 'unknown')}", file=sys.stderr)
        return 1
    if args.as_json:
        print(json.dumps(out, indent=1, sort_keys=True))
    else:
        _print_human(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
