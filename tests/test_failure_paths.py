"""Regression tests for failure paths that used to be swallowed."""

from __future__ import annotations

import asyncio
import json
import socket
import subprocess
import sys
from collections import deque
from pathlib import Path
from typing import Any

import pytest

import openstage.probe as probe
import openstage.write_cli as write_cli
from openstage import pdu, uuids
from openstage.capture import FORMAT, CaptureFormatError, load_capture
from openstage.client import (
    BikeConnectionError,
    BikeDisconnected,
    BikeIdentity,
    BikeIncompatibleError,
    BikeRadioError,
    BikeSubscriptionError,
    BikeTransientError,
    Notification,
    SB20Client,
    SettingsCommitError,
)
from openstage.replay import ReplayClient
from openstage.state import BikeState
from openstage.transport import BleakTransport
from openstage.tui.app import DashApp
from textual.widgets import RichLog
from openstage.wftnp import WftnpServer, WftnpStartError, preflight_port
from openstage.wftnp.bridge import WftnpBridge
from openstage.wftnp.mdns import Advertisement

FULL_CHARACTERISTICS = frozenset(
    {
        uuids.API_REQUEST,
        uuids.API_RESPONSE,
        uuids.BUTTON_ACTIVE,
        uuids.BRAKE_VALUE,
        uuids.FTMS_FEATURE,
        uuids.FTMS_INDOOR_BIKE_DATA,
        uuids.FTMS_STATUS,
        uuids.FTMS_CONTROL_POINT,
        uuids.CSC_MEASUREMENT,
    }
)


class _Transport:
    def __init__(
        self,
        *,
        characteristics: frozenset[str] = FULL_CHARACTERISTICS,
        fail_notify: bool = False,
        fail_stop: bool = False,
    ) -> None:
        self._connected = False
        self.mtu_size = 23
        self.service_uuids = frozenset(
            {uuids.API_SERVICE, uuids.BUTTON_SERVICE, uuids.FITNESS_MACHINE}
        )
        self.characteristic_uuids = characteristics
        self.fail_notify = fail_notify
        self.fail_stop = fail_stop
        self.callbacks: dict[str, Any] = {}
        self.writes: list[bytes] = []
        self.responses: dict[bytes, bytes] = {}
        self.disconnected_callback: Any = None
        self.disconnect_count = 0
        self.stop_attempts: list[str] = []

    @property
    def is_connected(self) -> bool:
        return self._connected

    def set_disconnected_callback(self, callback: Any) -> None:
        self.disconnected_callback = callback

    async def connect(self) -> None:
        self._connected = True

    async def disconnect(self) -> None:
        self._connected = False
        self.disconnect_count += 1

    async def read_characteristic(self, uuid: str) -> bytes:
        return b"x"

    async def write_characteristic(self, uuid: str, data: bytes) -> None:
        if uuid != uuids.API_REQUEST:
            return
        self.writes.append(data)
        response = self.responses.get(data)
        callback = self.callbacks.get(uuids.normalize(uuids.API_RESPONSE))
        if response is not None and callback is not None:
            callback(response)

    async def start_notify(self, uuid: str, callback: Any) -> None:
        if self.fail_notify and uuids.normalize(uuid) == uuids.normalize(
            uuids.API_RESPONSE
        ):
            raise OSError("notification enable failed")
        self.callbacks[uuids.normalize(uuid)] = callback

    async def stop_notify(self, uuid: str) -> None:
        self.stop_attempts.append(uuids.normalize(uuid))
        if self.fail_stop:
            raise OSError("notification disable failed")
        self.callbacks.pop(uuids.normalize(uuid), None)


def _client(transport: _Transport, **kwargs: Any) -> SB20Client:
    return SB20Client(
        transport=transport,
        request_timeout=0.1,
        subscribe_buttons=False,
        subscribe_ftms=False,
        subscribe_csc=False,
        **kwargs,
    )


async def test_missing_required_characteristic_is_incompatible() -> None:
    missing = FULL_CHARACTERISTICS - {uuids.API_RESPONSE}
    transport = _Transport(characteristics=frozenset(missing))
    client = _client(transport)

    with pytest.raises(BikeIncompatibleError, match="API_RESPONSE|0c46beb0"):
        await client.connect()

    assert transport.is_connected is False
    await client.close()


async def test_required_subscription_failure_is_retryable() -> None:
    transport = _Transport(fail_notify=True)
    client = _client(transport)

    with pytest.raises(BikeSubscriptionError, match="api"):
        await client.connect()

    assert transport.is_connected is False
    assert isinstance(BikeSubscriptionError("x"), BikeTransientError)
    await client.close()


async def test_disconnect_wakes_notification_consumer() -> None:
    transport = _Transport()
    client = _client(transport)
    await client.connect()

    async def consume() -> None:
        async for _note in client.notifications():
            pass

    task = asyncio.create_task(consume())
    await asyncio.sleep(0)
    assert transport.disconnected_callback is not None
    transport.disconnected_callback()
    with pytest.raises(BikeDisconnected):
        await task
    assert client.status == "disconnected"
    await client.close()


async def test_notifications_detect_a_missed_disconnect_callback() -> None:
    transport = _Transport()
    client = _client(transport)
    await client.connect()

    transport._connected = False
    iterator = client.notifications()
    with pytest.raises(BikeDisconnected):
        await asyncio.wait_for(anext(iterator), timeout=2.0)
    await iterator.aclose()
    await client.close()


async def test_disconnect_clears_subscriptions_before_cleanup() -> None:
    transport = _Transport()
    client = _client(transport)
    await client.connect()
    assert transport.disconnected_callback is not None

    transport._connected = False
    transport.disconnected_callback()
    assert client._started == {}

    await client.close()
    assert client.cleanup_errors == ()
    assert transport.stop_attempts == []


async def test_cleanup_attempts_stop_and_disconnect() -> None:
    transport = _Transport(fail_stop=True)
    client = _client(transport)
    await client.connect()

    await client.close()

    assert transport.disconnect_count == 1
    assert any("stop api notifications" in error for error in client.cleanup_errors)


class _HangingWrite(_Transport):
    async def write_characteristic(self, uuid: str, data: bytes) -> None:
        await asyncio.sleep(5)


async def test_store_write_timeout_does_not_hold_the_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("openstage.client._WRITE_TIMEOUT_S", 0.05)
    client = _client(_HangingWrite())
    await client.connect()

    with pytest.raises(BikeConnectionError, match="write failed"):
        await asyncio.wait_for(client.store_gearing(), timeout=1.0)

    await client.close()


async def test_store_failure_reports_the_completed_set() -> None:
    transport = _Transport()
    request = pdu.build_set_weight(85.0, 7.5)
    transport.responses[request.pdu] = bytes.fromhex("030134214006")
    client = _client(transport)
    await client.connect()

    with pytest.raises(SettingsCommitError) as caught:
        await client.set_weight(85.0, 7.5, store=True)

    assert caught.value.step == "store settings"
    assert len(caught.value.completed) == 1
    assert "may have been applied" in str(caught.value)
    await client.close()


def test_transport_disconnect_callback_is_reusable() -> None:
    calls: list[int] = []
    transport = BleakTransport.__new__(BleakTransport)
    transport._disconnected_callback = lambda: calls.append(1)  # type: ignore[attr-defined]
    transport._handle_disconnect(object())  # type: ignore[arg-type]
    transport._handle_disconnect(object())  # type: ignore[arg-type]
    assert calls == [1, 1]


def test_capture_rejects_malformed_frame(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text(
        json.dumps(
            {
                "format": FORMAT,
                "frames": [
                    {"t": 0.1, "stream": "api", "hex": "0102"},
                    {"t": 0.2, "stream": "api", "hex": "zz"},
                ],
            }
        )
    )
    with pytest.raises(CaptureFormatError, match=r"frames\[1\]"):
        load_capture(path)


def test_capture_rejects_unknown_format(tmp_path: Path) -> None:
    path = tmp_path / "future.json"
    path.write_text(json.dumps({"format": "openstage-capture/99"}))
    with pytest.raises(CaptureFormatError, match="unsupported capture format"):
        load_capture(path)


async def test_wftnp_start_cleans_up_when_advertisement_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail_start(self: Advertisement) -> None:
        raise RuntimeError("mDNS unavailable")

    monkeypatch.setattr(Advertisement, "start", fail_start)
    server = WftnpServer(
        name="Square Test",
        services=[],
        txt={},
        port=0,
        advertise=True,
    )

    with pytest.raises(WftnpStartError, match="mDNS unavailable"):
        await server.start()

    assert server._server is None
    assert server._advertisement is None


async def test_preflight_falls_back_when_default_port_is_busy() -> None:
    busy = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    busy.bind(("0.0.0.0", 0))
    busy.listen()
    busy_port = int(busy.getsockname()[1])
    try:
        chosen = await preflight_port(busy_port)
    finally:
        busy.close()

    assert chosen > 0
    assert chosen != busy_port


async def test_battery_is_clamped_for_the_wftnp_status() -> None:
    client = _client(_Transport())
    bridge = WftnpBridge(
        client,
        name="Square Test",
        txt={},
        port=0,
        advertise=False,
        standalone=False,
    )
    record = pdu.Battery(
        raw=b"\x07\x00\x7f",
        op=pdu.Op.BATTERY,
        source=pdu.BatterySource.POWER_METER,
        percent=127,
    )
    await bridge.handle(
        Notification(stream=pdu.Stream.API, raw=record.raw, record=record, t=0.0)
    )

    assert bridge._elite.encode_status()[2] == 100


class _RadioFail(SB20Client):
    def __init__(self) -> None:
        super().__init__()

    async def connect(self) -> None:
        raise BikeRadioError("adapter is off")

    async def close(self) -> None:
        return None


class _TransientFail(SB20Client):
    calls = 0

    def __init__(self) -> None:
        super().__init__()

    async def connect(self) -> None:
        type(self).calls += 1
        raise BikeTransientError("bike asleep")

    async def close(self) -> None:
        return None


async def test_tui_aborts_on_radio_error() -> None:
    state = BikeState()
    app = DashApp(
        _RadioFail(),
        state,
        deque(maxlen=100),
        allow_telemetry=False,
    )
    async with app.run_test(size=(100, 30)) as pilot:
        await asyncio.sleep(0.2)
        await pilot.pause()
        assert app.is_running is False
        assert app.return_code == 1
        assert "Bluetooth adapter unavailable" in state.status


async def test_tui_log_panel_fits_a_compact_terminal() -> None:
    fixture = Path(__file__).parent / "fixtures" / "legacy" / "queries.json"
    feed = ReplayClient(fixture, speed=1000)
    app = DashApp(
        feed,
        BikeState(),
        deque(maxlen=100),
        allow_telemetry=False,
    )
    async with app.run_test(size=(100, 24)) as pilot:
        await asyncio.sleep(0.2)
        await pilot.pause()
        log = app.screen.query_one("#log", RichLog)
        assert log.size.height >= 4
        assert log.region.bottom <= app.size.height


async def test_tui_keeps_retrying_transient_failures() -> None:
    _TransientFail.calls = 0
    state = BikeState()
    app = DashApp(
        _TransientFail(),
        state,
        deque(maxlen=100),
        allow_telemetry=False,
    )
    async with app.run_test(size=(100, 30)) as pilot:
        await asyncio.sleep(0.2)
        await pilot.pause()
        assert app.is_running is True
        assert _TransientFail.calls >= 1
        assert state.status == "reconnecting…"


class _ProbeClient:
    def __init__(self, **_: Any) -> None:
        self.identity = BikeIdentity(name="Bike", address="AA")
        self.mtu = 23
        self.status = "connected"
        self.notes: tuple[str, ...] = ()

    async def connect(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def query_all(self) -> dict[str, Exception]:
        return {
            request.name: TimeoutError("no answer") for request in pdu.QUERY_REQUESTS
        }

    def notifications(self) -> Any:
        async def empty() -> Any:
            if False:
                yield

        return empty()


async def test_probe_exits_nonzero_when_no_query_answers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(probe, "SB20Client", _ProbeClient)
    code = await probe._run_live(probe.ProbeOptions(seconds=0.0, telemetry=False))
    assert code == 1


def _bad_capture(path: Path) -> Path:
    path.write_text(
        json.dumps(
            {
                "format": FORMAT,
                "frames": [{"t": 0.1, "stream": "api", "hex": "zz"}],
            }
        )
    )
    return path


def test_probe_reports_capture_error(tmp_path: Path, capsys: Any) -> None:
    code = probe.main(["--replay", str(_bad_capture(tmp_path / "bad.json"))])
    assert code == 1
    assert "probe error:" in capsys.readouterr().err


def test_tui_reports_capture_error(tmp_path: Path, capsys: Any) -> None:
    from openstage.tui.app import main as tui_main

    code = tui_main(["--replay", str(_bad_capture(tmp_path / "bad.json"))])
    assert code == 1
    assert "openstage-tui:" in capsys.readouterr().err


def test_write_reports_readback_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    async def fake_run(_args: Any) -> dict[str, Any]:
        return {
            "readback": {"error": "timed out"},
            "state": "unknown: the write may have been applied",
        }

    monkeypatch.setattr(write_cli, "_run", fake_run)
    code = write_cli.main(["weight", "--user-kg", "85", "--yes"])

    assert code == 1
    err = capsys.readouterr().err
    assert "readback failed: timed out" in err
    assert "unknown: the write may have been applied" in err


def test_missing_zeroconf_fails_at_import_and_in_tui_entry() -> None:
    script = """
import importlib.abc
import sys

class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name == "zeroconf" or name.startswith("zeroconf."):
            raise ModuleNotFoundError(f"No module named {name!r}")
        return None

sys.meta_path.insert(0, Block())
try:
    import openstage.wftnp
except ImportError as exc:
    print("IMPORT", type(exc).__name__, str(exc))
else:
    print("IMPORT_UNEXPECTED")

from openstage.tui.entry import main
print("TUI", main(["--help"]))
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parents[1],
    )

    assert result.returncode == 0
    assert "WftnpDependencyError" in result.stdout
    assert "requires zeroconf" in result.stdout
    assert "TUI 1" in result.stdout
    assert "openstage-tui: cannot start" in result.stderr
