# `openstage.client`

## `SB20Client`

```python
SB20Client(
    *,
    transport: Transport | None = None,
    address: str | None = None,
    name_filter: str | None = None,
    scan_timeout: float = 5.0,
    request_timeout: float = 5.0,
    store_timeout: float = 10.0,
    gearing_store_timeout: float = 240.0,
    gearing_apply_timeout: float = 30.0,
    zero_reset_timeout: float = 30.0,
    subscribe_buttons: bool = True,
    subscribe_ftms: bool = True,
    subscribe_csc: bool = True,
)
```

The client owns one BLE connection, the notification streams and the request
lock. It is an async context manager:

```python
async with SB20Client(address="AA:BB:CC:DD:EE:FF") as bike:
    print(bike.status)
```

| Parameter | Meaning |
|---|---|
| `transport` | Pre-built transport. When supplied, the client does not scan and does not create a `BleakTransport`. |
| `address` | Connect to this address and skip discovery. |
| `name_filter` | During discovery, accept only advertised names containing this text. |
| `scan_timeout` | Length of one scan attempt. Discovery repeats until a bike is found. |
| `request_timeout` | Response wait for an ordinary request, unless a method passes its own limit. |
| `store_timeout` | Settings-store timeout. |
| `gearing_store_timeout` | Gearing flash-store timeout; the default is 240 s because a store can be slow. |
| `gearing_apply_timeout` | Timeout for the gearing APPLY acknowledgement. |
| `zero_reset_timeout` | Timeout while waiting for both calibration offsets. |
| `subscribe_buttons` | Start notifications on the button and brake characteristics. |
| `subscribe_ftms` | Start notifications on FTMS Indoor Bike Data and FTMS Status. |
| `subscribe_csc` | Start notifications on the CSC measurement. |

The control-service response characteristic is always subscribed. A missing
optional characteristic is recorded in `notes` and does not fail the connection.

### Properties

| Property | Type | Meaning |
|---|---|---|
| `mode` | `str` | `"live"` for an `SB20Client`, `"replay"` for a `ReplayClient`. |
| `status` | `str` | Human-readable connection state, updated during connect and close. |
| `identity` | `BikeIdentity` | Device Information values plus the advertised name and address. |
| `mtu` | `int \| None` | Negotiated ATT MTU, when the transport reports it. |
| `notes` | `tuple[str, ...]` | Recent connection and listener notes, capped at 200 entries. |
| `transport` | `Transport \| None` | The active transport, if one has been created. |

`BikeIdentity` is a frozen dataclass with `name`, `address`, `model`, `serial`,
`firmware`, `hardware`, `software` and `manufacturer`. `as_dict()` returns the
same fields as a dictionary.

### Lifecycle

#### `await client.connect()`

Discovers a bike when needed, connects, verifies that the control service is
present, reads Device Information, and starts the notification streams. It is
safe to call on an already connected client.

Raises `BikeConnectionError` if the radio or transport fails, and `BikeNotFound`
if the connected device has no control service.

#### `await client.close()`

Closes telemetry, stops notifications and disconnects. Safe to call more than
once. Closing ends the `notifications()` iterator.

#### `async with SB20Client(...) as bike:`

Calls `connect()` on entry and `close()` on exit.

### Notifications

#### `client.notifications() -> AsyncGenerator[Notification, None]`

Yields every decoded notification until the client is closed. The queue is
unbounded for the lifetime of the client, so consume it or use a listener.

#### `client.add_listener(listener)` and `client.remove_listener(listener)`

A listener is a synchronous callable that receives each `Notification`. A
listener exception is caught and recorded in `notes`; it does not break the BLE
callback. Listeners run before the notification is queued.

```python
Notification(stream: Stream, raw: bytes, record: pdu.Pdu, t: float)
```

| Field | Meaning |
|---|---|
| `stream` | Which characteristic produced the value. |
| `raw` | The complete characteristic value. |
| `record` | The decoded PDU. |
| `t` | `time.monotonic()` timestamp. |
| `hex` | Property with the raw value as lowercase hex. |

### Low-level requests

#### `await client.request(request: ReadRequest) -> pdu.Pdu`

Sends one allow-listed read request and waits for the response. Raises
`SafetyError` if the bytes are not in the read-only allow-list,
`BikeTimeout` if the bike does not answer, and `BikeProtocolError` if the bike
returns an error.

#### `await client.write_request(request: WriteRequest, *, timeout=None) -> pdu.Pdu`

Sends one allow-listed write and waits for its response. This is the escape
hatch for a builder from `openstage.pdu`. Prefer the named methods below,
because they also check the response type.

### Query methods

All query methods send one request and return the matching typed record.

| Method | Returns | What it reads |
|---|---|---|
| `get_scts_identity()` | `SctsIdentity` | Control-channel identity and version. |
| `get_bike_status()` | `BikeStatus` | Flywheel speed, HRM status, power-meter status and pairing status. |
| `get_pairing()` | `PowerMeterPairing` | Crank length, left/right ANT IDs and serial numbers. |
| `get_parameters()` | `Parameters` | Brake and dual-BLE tri-state values. |
| `get_resistance()` | `Resistance` | Current resistance level. |
| `get_gradient_scaling()` | `GradientScaling` | Gradient scaling factor and delay flag. |
| `get_weight()` | `Weight` | User and equipment weight. |
| `get_battery()` | `Battery` | Power-meter battery percentage. |
| `get_buttons()` | `ButtonAssignments` | The 15 button-assignment wire values. |
| `get_current_gear()` | `GearNotification` | Current gear, answered by a gear notification. |
| `get_model_header()` | `GearModelHeader` | The stored drivetrain model header. |
| `get_sensor_firmware(side)` | `SensorFirmware` | Firmware version for `"left"` or `"right"`. |
| `query_all()` | `dict[str, pdu.Pdu \| Exception]` | Runs every query and keeps per-query failures. |

`query_all()` keys are the request names from `pdu.QUERY_REQUESTS`, including
`"bike_status"`, `"weight"`, `"buttons"`, `"current_gear"` and
`"gear_model"`. A failed query is stored as its exception and appended to
`notes`; the method itself does not raise.

### Configuration writes

Each write returns `WriteResult`. With `store=True`, the client sends the
settings store (`0xFD/0`) after the Set and includes a `StoreResult`.

| Method | Wire effect | Range and defaults |
|---|---|---|
| `set_weight(user_kg, equipment_kg, *, store=False)` | Sets both weights. | 0…655.35 kg per field, unit 0.01 kg. |
| `set_parameters(brake_enabled, dual_ble_enabled, *, store=False)` | Sets brake and dual-BLE. | `None` leaves a field unchanged. |
| `set_gradient_scaling(factor, delay_enabled=False, *, store=False)` | Sets the gradient factor. | `factor` 0…65535, unit 0.5 %. |
| `set_target_resistance(percent)` | Selects resistance mode at a level. | Percent 0…100, converted with `round(percent * 255 / 100)`. |
| `set_crank_length(mm, *, store=False)` | Sets crank length and restarts power-meter pairing. | 165, 170, 172.5 or 175 mm. |
| `set_target_power(watts)` | Selects ERG at a target. | 0…1000 W, wire unit 0.5 W. |
| `set_target_grade(percent)` | Selects simulation mode at a grade. | −20…+20 %, wire unit 0.001 %. |
| `set_control_mode(mode, target=0.0)` | Dispatches to one of the three control writes. | See below. |
| `erg_on(watts)` | Alias for `set_target_power(watts)`. | Same as ERG. |
| `erg_off()` | Sends simulation grade `0.0`. | Leaves ERG and re-enables shifting. |
| `zero_reset()` | Starts calibration and waits for offsets. | Returns `ZeroReset`; see below. |
| `set_buttons(values, *, store=False)` | Sets all 15 wire values. | Values 0…43. |
| `set_button_actions(actions, *, store=False)` | Sets 14 physical buttons from `ButtonAction` values. | Values are the action enum members. |
| `set_button_slots(changes, *, store=False)` | Changes selected physical buttons 1…14. | Other assignments are preserved from a read-back. |
| `store_settings()` | Sends the settings store and waits for a terminal reply. Raises `BikeTimeout` when none arrives within `store_timeout`. | Returns `StoreResult`. |

`set_control_mode()` takes a `ControlMode`:

| Mode | `target` | Meaning |
|---|---|---|
| `ControlMode.ERG` | watts | Hold a target power. The bike ignores shifts. |
| `ControlMode.RESISTANCE` | percent | Apply a resistance level. Shifts remain active. |
| `ControlMode.SIMULATION` | percent grade | Apply a simulated grade. Shifts remain active. |

A target of zero still selects the mode. `erg_off()` uses simulation grade zero
because there is no separate ERG-off command.

#### `zero_reset()`

Sends `06 00` and waits until both power meters report a calibration offset.
The bike may answer with two success frames, one per side. The method merges
them and returns one `ZeroReset`. A terminal error returns immediately.

If the timeout expires after at least one side replied, the method raises
`ZeroResetTimeout` with the partial `result` attached. There is no cancel
command; a started reset runs to completion or times out.

### Telemetry

| Method | Effect |
|---|---|
| `subscribe_power_data()` | Asks the bike to emit power-data events and records the subscription. |
| `unsubscribe_power_data()` | Stops power-data events. |
| `subscribe_gearing()` | Asks the bike to emit gear notifications. |
| `unsubscribe_gearing()` | Stops gear notifications. |
| `open_telemetry(*, power=True, gearing=True)` | Subscribes and records failures in `notes` instead of raising. |
| `close_telemetry()` | Unsubscribes everything recorded as active. |

Subscriptions control the bike's event generation. The response
characteristic is already subscribed at connect.

### Builders

| Method | Returns |
|---|---|
| `settings()` | `BikeSettings` for diff-based configuration writes. |
| `gears()` | `GearSettings` for a drivetrain plan. |

See [Settings](settings.md) and [Drivetrain](gearing.md).

## `BikeFeed`

`BikeFeed` is a protocol implemented by `SB20Client` and `ReplayClient`. The
dashboard and probe depend only on this surface.

```python
class BikeFeed(Protocol):
    @property
    def mode(self) -> str: ...
    @property
    def status(self) -> str: ...
    @property
    def identity(self) -> BikeIdentity: ...
    @property
    def mtu(self) -> int | None: ...
    async def connect(self) -> None: ...
    async def close(self) -> None: ...
    def notifications(self) -> AsyncGenerator[Notification, None]: ...
    async def query_all(self) -> dict[str, pdu.Pdu | Exception]: ...
```

## Enums

### `ControlMode`

```python
ControlMode.ERG          # "erg"
ControlMode.RESISTANCE   # "resistance"
ControlMode.SIMULATION   # "simulation"
```

### `DrivetrainMode`

```python
DrivetrainMode.DREAM_DRIVE        # "dream_drive"
DrivetrainMode.SIMULATED_CASSETTE # "simulated_cassette"
```

## Errors

| Class | Base | Raised when |
|---|---|---|
| `BikeError` | `RuntimeError` | Base class for all library errors. |
| `BikeNotFound` | `BikeError` | No SB20-shaped device was found, or the control service is absent. |
| `BikeConnectionError` | `BikeError` | The transport is not connected or a write/read failed. |
| `BikeTimeout` | `BikeError` | The bike did not answer in time. |
| `ZeroResetTimeout` | `BikeTimeout` | Calibration timed out after at least one side replied. `result` holds the partial record. |
| `BikeProtocolError` | `BikeError` | The bike returned an error PDU or an unexpected record. |
| `BikePermissionError` | `BikeProtocolError` | Reserved permission-denied PDU (`0xFA`) was received. |
| `BikeAlreadyConnectedError` | `BikeProtocolError` | Reserved already-connected PDU (`0xFB`) was received. `controller_address` is not exposed separately; the message contains it. |
| `SafetyError` | `RuntimeError` | A caller tried to send a PDU outside the allow-list. |

`BikePermissionError` and `BikeAlreadyConnectedError` exist for completeness. The
SB20 does not send either PDU in normal operation.

## Result types

### `WriteResult`

```python
WriteResult(set_response: pdu.Pdu, store: StoreResult | None = None)
```

| Member | Meaning |
|---|---|
| `set_response` | The typed response to the Set. |
| `store` | The settings-store result when `store=True` was requested. |
| `persisted` | True when `store` exists and reports `stored`. |

### `StoreResult`

```python
StoreResult(messages: tuple[StoreSettings, ...])
```

| Member | Meaning |
|---|---|
| `messages` | Every `FD` message observed for this store request. |
| `command` | The last `StoreSettingsCommand`, or `None`. |
| `stored` | Any message is `STORED` or `STORED_AND_APPLIED`. |
| `busy` | The bike answered `ERROR`. |

The client waits for `ERROR`, `STORED` or `STORED_AND_APPLIED`. Messages that
arrived before the store request are ignored.

## Example: hold a session open

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


asyncio.run(main())
```
