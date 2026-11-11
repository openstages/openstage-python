# `openstage.state`

`BikeState` turns notifications into values a user interface can display. It
knows nothing about BLE, Textual or raw bytes beyond the `Notification` it
receives.

```python
import asyncio

from openstage import BikeState, SB20Client


async def main() -> None:
    state = BikeState()
    async with SB20Client() as bike:
        state.identity = bike.identity
        state.mtu = bike.mtu
        await bike.open_telemetry()
        async for note in bike.notifications():
            state.apply(note)
            print(state.power_w.value, state.cadence_rpm.value)


asyncio.run(main())
```

## `BikeState`

### Fields

| Field | Type | Meaning |
|---|---|---|
| `identity` | `BikeIdentity` | Device identity. |
| `mtu` | `int \| None` | Negotiated MTU. |
| `status` | `str` | Connection or replay status. |
| `power` | `Metric[int]` | Power by stream. |
| `cadence` | `Metric[float]` | Cadence by stream. |
| `balance` | `Metric[int]` | Power distribution percentage. |
| `lr` | `Metric[int]` | Left/right difference. |
| `speed` | `Metric[float]` | Speed in km/h. |
| `distance` | `Metric[int]` | Distance in metres. |
| `preferences` | `SourcePreferences` | Source selection per metric. |
| `flywheel_rpm` | `Reading[float]` | Flywheel speed. |
| `hrm` | `Reading[SensorStatus]` | Heart-rate monitor status. |
| `power_meter` | `Reading[SensorStatus]` | Power-meter status. |
| `pairing_status` | `Reading[PairingStatus]` | Pairing status. |
| `resistance` | `Reading[Resistance]` | Last resistance read-back. |
| `gradient_scaling` | `Reading[GradientScaling]` | Last gradient scaling read-back. |
| `weight` | `Reading[Weight]` | Last weight read-back. |
| `parameters` | `Reading[Parameters]` | Last brake/dual-BLE read-back. |
| `pairing` | `Reading[PowerMeterPairing]` | Last pairing read-back. |
| `battery` | `Reading[Battery]` | Last battery read-back. |
| `buttons` | `Reading[ButtonAssignments]` | Last button-assignment read-back. |
| `sensor_firmware_left` | `Reading[SensorFirmware]` | Left firmware version. |
| `sensor_firmware_right` | `Reading[SensorFirmware]` | Right firmware version. |
| `gear` | `Reading[GearNotification]` | Current gear. |
| `model_header` | `GearModelHeader \| None` | Drivetrain header. |
| `map_bytes` | `bytes` | Drivetrain map collected so far. |
| `teeth_front` | `dict[int, int]` | Front teeth keyed by 1-based index. |
| `teeth_rear` | `dict[int, int]` | Cassette teeth keyed the same way. |
| `coefficients` | `dict[int, int]` | Dream Drive coefficients by 1-based index. |
| `button_mask` | `int` | Current pressed-button mask. |
| `button_events` | `int` | Number of button frames folded. |
| `button_t` | `float` | Time of the last button frame. |
| `button_log` | `deque[tuple[int, int, int]]` | Recent `(selector, mask, second_field)` entries. |
| `brake_left` | `Reading[int]` | Left brake lever. |
| `brake_right` | `Reading[int]` | Right brake lever. |
| `scts` | `SctsIdentity \| None` | Last identity response. |
| `last_error` | `ErrorPdu \| None` | Last error PDU. |
| `counts` | `dict[Stream, int]` | Frames seen per stream. |

### Resolved metrics

These properties resolve the configured source and return a `Reading`:

| Property | Returns |
|---|---|
| `power_w` | Power in watts. |
| `cadence_rpm` | Cadence in rpm. |
| `balance_pct` | Power distribution percentage. |
| `lr_difference` | Left/right difference. |
| `speed_kph` | Speed in km/h. |
| `distance_m` | Distance in metres. |

`auto` prefers the control service for power, cadence, balance and left/right
difference. Speed and distance have no control-service value, so they come from
FTMS. In `auto` mode, a preferred value older than 2 seconds loses to a fresh
value from another source. If every source is stale, the first present value is
still returned so the display does not blank out between packets. A specific
source preference never falls back to another source.

### `apply(note: Notification) -> str`

Folds one notification and returns a short event description for a log. It
increments `counts` for the stream.

### `ingest(record: pdu.Pdu, stream: Stream, t: float | None = None) -> str`

Folds a decoded record. Use this when you already have a record and do not need
the `Notification` wrapper. A missing `t` uses `time.monotonic()`.

### `ingest_sensor_firmware(side, record, t=None) -> None`

Tags a firmware record with `"left"` or `"right"`. The firmware response does
not identify the side by itself, so the caller supplies it.

### `pressed_mask(stale_after=0.4) -> int`

Returns the current button mask, or zero when the last button frame is older
than `stale_after` seconds.

### `set_known_drivetrain(header, map_bytes) -> None`

Installs a map that was just applied. Use this after a successful gearing
commit because the bike cannot read the active map back.

### `drivetrain_view() -> DrivetrainView`

Returns an immutable snapshot with the model, counts, CRC, map progress, teeth
and coefficients.

### `metric_sources(name) -> tuple[str, ...]`

Returns the sources that have reported a metric, for example
`state.metric_sources("power")`.

### `to_dict() -> dict[str, object]`

Returns a JSON-friendly snapshot for the probe and the dashboard's `--json`
mode. Readings become `{"value", "source", "age_s"}` objects.

## `Reading`

```python
Reading(value: T | None = None, source: str = "", updated_at: float | None = None)
```

| Member | Meaning |
|---|---|
| `value` | Last value, or `None`. |
| `source` | Source label. |
| `updated_at` | `time.monotonic()` timestamp. |
| `age` | Seconds since the update, or `None`. |
| `set(value, source, t)` | Store a value. A `None` value is ignored. |

## `Metric`

```python
Metric(readings: dict[str, Reading[T]] = {})
```

| Member | Meaning |
|---|---|
| `readings` | One `Reading` per source. |
| `set(value, source, t)` | Store a value for one source. |
| `resolve(preference, order, *, max_age=2.0)` | Select a reading. |

`resolve()` returns the named preference when it is not `"auto"`. For `"auto"`,
it walks `order` and returns the first fresh value. If none is fresh, it returns
the first present value.

## `SourcePreferences`

```python
SourcePreferences(
    power=API_POWER_SOURCE,
    cadence=API_POWER_SOURCE,
    speed=FTMS_SOURCE,
    distance=FTMS_SOURCE,
    balance=API_POWER_SOURCE,
    lr=API_POWER_SOURCE,
)
```

| Constant | Value |
|---|---|
| `AUTO_SOURCE` | `"auto"` |
| `API_POWER_SOURCE` | `"API 0x08/2"` |
| `FTMS_SOURCE` | `"FTMS 0x2AD2"` |
| `CSC_SOURCE` | `"CSC 0x2A5B"` |
| `SOURCE_ORDER` | Preference order per metric. |
| `SOURCE_LABELS` | Display labels including `auto`. |

Cadence can also come from CSC. The CSC path derives cadence from crank
revolutions and event time; in `auto` mode it comes after the control service
and FTMS.

## `DrivetrainView`

```python
DrivetrainView(
    model: ModelType | None,
    front_count: int | None,
    rear_count: int | None,
    crc8: int | None,
    step: int | None,
    dream_drive: bool | None,
    map_size: int | None,
    map_bytes: int,
    map_complete: bool,
    teeth_front: Mapping[int, int],
    teeth_rear: Mapping[int, int],
    coefficients: Mapping[int, int],
)
```

`map_complete` is true when the collected map has reached `map_size`. Dream
Drive maps can be complete from the header alone when the deterministic map
matches the header CRC.
