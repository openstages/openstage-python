"""Session capture format used by the probe, replay and TUI snapshot modes.

A capture is deliberately plain JSON: the raw characteristic values are the
ground truth, and adding decoded state is the reader's job. The loader also
accepts an older capture shape that stores the same frames and exchanges under
a ``meta`` object.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import pdu
from .client import BikeIdentity
from .pdu import Stream

FORMAT = "openstage-capture/1"


@dataclass(frozen=True, slots=True)
class CapturedFrame:
    t: float
    stream: Stream
    raw: bytes

    @property
    def hex(self) -> str:
        return self.raw.hex()


@dataclass(frozen=True, slots=True)
class CapturedExchange:
    request: bytes
    responses: tuple[bytes, ...]
    t: float = 0.0

    @property
    def hex(self) -> str:
        return self.request.hex()


@dataclass(frozen=True, slots=True)
class Capture:
    identity: BikeIdentity = field(default_factory=BikeIdentity)
    mtu: int | None = None
    frames: tuple[CapturedFrame, ...] = ()
    exchanges: tuple[CapturedExchange, ...] = ()
    notes: tuple[str, ...] = ()
    captured_at: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "format": FORMAT,
            "captured_at": self.captured_at or datetime.now(UTC).isoformat(timespec="seconds"),
            "identity": self.identity.as_dict(),
            "mtu": self.mtu,
            "notes": list(self.notes),
            "exchanges": [
                {
                    "t": exchange.t,
                    "request": exchange.hex,
                    "responses": [raw.hex() for raw in exchange.responses],
                }
                for exchange in self.exchanges
            ],
            "frames": [
                {"t": frame.t, "stream": frame.stream.value, "hex": frame.hex}
                for frame in self.frames
            ],
        }

    def write(self, path: str | Path) -> None:
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(self.to_json(), indent=1) + "\n", encoding="utf-8")


def load_capture(path: str | Path) -> Capture:
    source = Path(path)
    data: Any = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{source}: capture root must be an object")
    if data.get("format") == FORMAT:
        return _load_current(data)
    return _load_legacy(data)


def _parse_frames(items: Any) -> tuple[CapturedFrame, ...]:
    frames: list[CapturedFrame] = []
    for item in items or []:
        try:
            stream = Stream(str(item["stream"]))
            raw = bytes.fromhex(str(item["hex"]))
            t = float(item["t"])
        except (KeyError, TypeError, ValueError):
            continue
        frames.append(CapturedFrame(t=t, stream=stream, raw=raw))
    return tuple(sorted(frames, key=lambda frame: frame.t))


def _parse_current_exchanges(items: Any) -> tuple[CapturedExchange, ...]:
    exchanges: list[CapturedExchange] = []
    for item in items or []:
        try:
            request = bytes.fromhex(str(item["request"]))
            responses = tuple(bytes.fromhex(str(value)) for value in item.get("responses") or [])
        except (KeyError, TypeError, ValueError):
            continue
        exchanges.append(CapturedExchange(request=request, responses=responses,
                                          t=float(item.get("t", 0.0))))
    return tuple(exchanges)


def _parse_legacy_exchanges(items: Any) -> tuple[CapturedExchange, ...]:
    exchanges: list[CapturedExchange] = []
    for item in items or []:
        request_text = item.get("req_hex") or item.get("request")
        if not request_text:
            candidate = item.get("pdu")
            if isinstance(candidate, str) and all(
                character in "0123456789abcdefABCDEF" for character in candidate
            ):
                request_text = candidate
        try:
            request = bytes.fromhex(str(request_text or ""))
            raw_responses = item.get("resp_hex") or item.get("responses") or []
            responses = tuple(bytes.fromhex(str(value)) for value in raw_responses)
        except (KeyError, TypeError, ValueError):
            continue
        if not request:
            continue
        exchanges.append(CapturedExchange(request=request, responses=responses,
                                          t=float(item.get("t", 0.0))))
    return tuple(exchanges)


def _identity_from(values: dict[str, Any]) -> BikeIdentity:
    known = set(BikeIdentity.__dataclass_fields__)
    return BikeIdentity(**{key: str(value) for key, value in values.items()
                           if key in known and value is not None})


def _load_current(data: dict[str, Any]) -> Capture:
    identity_data = data.get("identity") or {}
    if not isinstance(identity_data, dict):
        identity_data = {}
    identity = _identity_from(identity_data)
    mtu = data.get("mtu")
    return Capture(
        identity=identity,
        mtu=int(mtu) if isinstance(mtu, int) else None,
        frames=_parse_frames(data.get("frames")),
        exchanges=_parse_current_exchanges(data.get("exchanges")),
        notes=tuple(str(value) for value in data.get("notes") or []),
        captured_at=str(data.get("captured_at")) if data.get("captured_at") else None,
    )


def _load_legacy(data: dict[str, Any]) -> Capture:
    """Read the older capture shape (``meta``/``frames``/``exchanges``)."""

    raw_meta = data.get("meta")
    meta: dict[str, Any] = raw_meta if isinstance(raw_meta, dict) else {}
    identity_data = dict(meta.get("identity") or {})
    device_name = data.get("device_name") or meta.get("device_name")
    address = data.get("address") or meta.get("address")
    if device_name:
        identity_data.setdefault("name", device_name)
    if address:
        identity_data.setdefault("address", address)
    identity = _identity_from(identity_data)
    mtu = data.get("mtu", meta.get("mtu"))
    return Capture(
        identity=identity,
        mtu=int(mtu) if isinstance(mtu, int) else None,
        frames=_parse_frames(data.get("frames")),
        exchanges=_parse_legacy_exchanges(data.get("exchanges")),
        notes=tuple(str(value) for value in data.get("notes") or []),
    )


def decode_frame(frame: CapturedFrame) -> pdu.Pdu:
    """Convenience for scripts inspecting a capture without a replay session."""

    return pdu.decode_notification(frame.stream, frame.raw)
