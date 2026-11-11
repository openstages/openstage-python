"""Discovery loop tests without a radio."""

from __future__ import annotations

import asyncio
from typing import Any

from openstage import uuids, transport


class _Device:
    def __init__(self, name: str, address: str) -> None:
        self.name = name
        self.address = address


class _Advertisement:
    def __init__(self, local_name: str, service_uuids: list[str]) -> None:
        self.local_name = local_name
        self.service_uuids = service_uuids
        self.rssi = -42


class _Scanner:
    starts = 0
    stops = 0

    def __init__(self, detection_callback: Any) -> None:
        self._callback = detection_callback

    async def start(self) -> None:
        type(self).starts += 1
        if type(self).starts >= 2:
            self._callback(
                _Device("Stages Bike 0071", "AA:BB:CC:DD:EE:FF"),
                _Advertisement("Stages Bike 0071", [uuids.FITNESS_MACHINE]),
            )

    async def stop(self) -> None:
        type(self).stops += 1


def test_discover_retries_after_empty_scan_and_stops_on_first_bike(monkeypatch: Any) -> None:
    _Scanner.starts = 0
    _Scanner.stops = 0
    monkeypatch.setattr(transport, "BleakScanner", _Scanner)

    found = asyncio.run(transport.discover_bikes(timeout=0.01))

    assert _Scanner.starts == 2
    assert _Scanner.stops == 2
    assert [bike.name for bike in found] == ["Stages Bike 0071"]
    assert found[0].address == "AA:BB:CC:DD:EE:FF"
