"""Run a WFTNP endpoint for a locally connected SB20.

The process owns the Bluetooth connection, so it closes the client on SIGINT
and SIGTERM. Killing it without that leaves BlueZ holding the link, and the
bike then refuses the next connection.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal

from ..client import SB20Client
from .bridge import DEFAULT_MAC, DEFAULT_NAME, DEFAULT_SERIAL, WftnpBridge
from .elite import SERVICE
from .server import DEFAULT_PORT

DESCRIPTION = "Run a WFTNP endpoint for a locally connected SB20."


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=DESCRIPTION)
    parser.add_argument("--address", help="BLE address of the bike; scan when omitted")
    parser.add_argument("--name", default=DEFAULT_NAME, help="mDNS instance name")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--serial", default=DEFAULT_SERIAL, help="serial-number TXT value")
    parser.add_argument("--mac", default=DEFAULT_MAC, help="mac-address TXT value")
    parser.add_argument(
        "--no-advertise", action="store_true", help="serve without mDNS"
    )
    parser.add_argument(
        "--debug", action="store_true", help="log every WFTNP frame"
    )
    return parser


async def _serve(args: argparse.Namespace) -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    txt = {
        "serial-number": args.serial,
        "mac-address": args.mac,
        "ble-service-uuids": SERVICE,
    }
    async with SB20Client(address=args.address) as bike:
        bridge = WftnpBridge(
            bike,
            name=args.name,
            txt=txt,
            port=args.port,
            advertise=not args.no_advertise,
        )
        await bridge.start()
        print(f"{args.name} serving on port {bridge.server.port}", flush=True)
        await stop.wait()
        await bridge.stop()


def main() -> int:
    args = _parser().parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    asyncio.run(_serve(args))
    return 0
