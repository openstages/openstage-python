"""Shared command-line connection options."""

from __future__ import annotations

import argparse


def add_connection_arguments(parser: argparse.ArgumentParser) -> None:
    """Add the BLE address, name filter, scan and request timeouts."""

    parser.add_argument("--address", help="connect to this BLE address, skipping the scan")
    parser.add_argument("--name", help="only accept advertised names containing this text")
    parser.add_argument("--scan-timeout", type=float, default=5.0,
                        help="seconds per scan attempt; scanning retries until a bike is found")
    parser.add_argument("--request-timeout", type=float, default=5.0,
                        help="seconds to wait for one API response")
