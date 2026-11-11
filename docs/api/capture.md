# Capture and replay

A capture is a JSON file with the raw characteristic values and the query
exchanges from one session. Raw values are the source of truth; decoding
happens when the capture is loaded.

## Format

```json
{
  "format": "openstage-capture/1",
  "captured_at": "2026-01-01T12:00:00+00:00",
  "identity": {"name": "Stages Bike", "address": "AA:BB:CC:DD:EE:FF"},
  "mtu": 23,
  "notes": ["connected; MTU 23"],
  "exchanges": [
    {"t": 0.0, "request": "0e00", "responses": ["0e000000000201"]}
  ],
  "frames": [
    {"t": 0.1, "stream": "api", "hex": "0c0100010d22000b000004"}
  ]
}
```

The allowed `stream` values are `api`, `buttons`, `brake`, `ftms_data`,
`ftms_status` and `csc`. Times are seconds from the start of the capture.

The loader also reads an older shape where the identity lives in a `meta`
object and exchanges use `req_hex` and `resp_hex`. Both shapes produce the same
`Capture`.

## `CapturedFrame`

```python
CapturedFrame(t: float, stream: Stream, raw: bytes)
```

One notification value. `hex` returns the raw bytes as lowercase hex.

## `CapturedExchange`

```python
CapturedExchange(request: bytes, responses: tuple[bytes, ...], t: float = 0.0)
```

One request and the response values observed for it. `hex` returns the request
as lowercase hex.

## `Capture`

```python
Capture(
    identity: BikeIdentity = BikeIdentity(),
    mtu: int | None = None,
    frames: tuple[CapturedFrame, ...] = (),
    exchanges: tuple[CapturedExchange, ...] = (),
    notes: tuple[str, ...] = (),
    captured_at: str | None = None,
)
```

| Member | Meaning |
|---|---|
| `to_json()` | Convert to the JSON-ready dictionary. |
| `write(path)` | Write indented JSON, creating parent directories. |

### `load_capture(path) -> Capture`

Reads a capture file. A file with the `openstage-capture/1` marker is read
directly; any other object is read as the older `meta` shape. Malformed
individual entries are skipped rather than failing the whole file. Raises
`ValueError` when the root is not an object.

### `decode_frame(frame) -> pdu.Pdu`

Decodes one captured frame without starting a replay session.

## `ReplayClient`

```python
ReplayClient(capture: Capture | str | Path, *, speed=1.0, loop=False)
```

`ReplayClient` implements `BikeFeed`. It feeds the query responses first, then
the recorded frames at their original spacing divided by `speed`.

| Member | Meaning |
|---|---|
| `mode` | `"replay"`. |
| `status` | Replay progress. |
| `identity` | Capture identity. |
| `mtu` | Capture MTU. |
| `capture` | The loaded `Capture`. |
| `connect()` | Sets the status line. No radio is used. |
| `close()` | Stops the iterator. |
| `query_all()` | Returns an empty dictionary; responses are already in the capture. |
| `notifications()` | Yields `Notification` values until the capture ends or the client closes. |

With `loop=True`, replay starts again after a short pause.

```python
import asyncio

from openstage import BikeState
from openstage.replay import ReplayClient


async def main() -> None:
    state = BikeState()
    client = ReplayClient("ride.json", speed=10)
    await client.connect()
    try:
        async for note in client.notifications():
            state.apply(note)
    finally:
        await client.close()


asyncio.run(main())
```
