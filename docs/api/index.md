# API reference

The public API lives in the `openstage` package. Everything is async where
it touches the radio, and synchronous where it only builds or decodes a value.

```python
from openstage import SB20Client, BikeState
from openstage import pdu
```

## Module map

| Module | Contents |
|---|---|
| `openstage.client` | `SB20Client`, fluent settings and drivetrain builders, result types, errors, `Notification`, `BikeIdentity`, `ControlMode`. |
| `openstage.pdu` | Typed records, enums, decoders, request constants, write builders and the write allow-list. |
| `openstage.state` | `BikeState`, `Reading`, `Metric`, `SourcePreferences`, `DrivetrainView`. |
| `openstage.transport` | `Transport` protocol, `BikeAdvertisement`, `discover_bikes`, `BleakTransport`. |
| `openstage.capture` | `Capture`, `CapturedFrame`, `CapturedExchange`, `load_capture`. |
| `openstage.replay` | `ReplayClient`. |
| `openstage.gearing_data` | Bundled drivetrain tables and stored-preset recovery. |
| `openstage.uuids` | GATT UUIDs used by the bike. |
| `openstage.wftnp` | WFTNP endpoint: frame codec, GATT server, Elite Square, FTMS proxy, weight-only Stages service and the bridge. |
| `openstage.probe` | `openstage-probe` entry point. |
| `openstage.write_cli` | `openstage-write` entry point. |
| `openstage.tui` | Terminal dashboard. |

Most names are re-exported from `openstage`, so `from openstage import
SB20Client` is the normal import. Use the submodule path when you need a name
that is not in the package namespace.

## Conventions

| Topic | Convention |
|---|---|
| Units | The library uses user-facing units such as watts, rpm, km/h, metres, kilograms, millimetres, percent and degrees of grade. Wire scaling is applied inside `pdu.py`. |
| Time | Notification timestamps are `time.monotonic()` seconds. |
| Errors | All library errors derive from `BikeError`. |
| Raw data | Every decoded record keeps the complete characteristic value in `raw`. |
| Request matching | One request may be outstanding per connection. Responses are matched by operation and command. |
| Writes | A configuration write is a `WriteRequest`; the client checks it against the allow-list before sending. |
| Persistence | A Set writes RAM. `store_settings()` writes the settings record to flash. The gearing map uses `store_gearing()`. |

## Pages

- [Client](client.md)
- [Settings](settings.md)
- [Drivetrain](gearing.md)
- [Records and builders](records.md)
- [State](state.md)
- [Transport](transport.md)
- [Capture and replay](capture.md)

The wire formats are documented separately in the
[control and telemetry service](../stages-sb20-control-telemetry-service.md)
and [button and brake service](../stages-sb20-button-brake-service.md)
specifications.
