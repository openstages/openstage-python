# openstage

`openstage` is an async Python library and terminal dashboard for the
Stages SB20 indoor bike. It talks to the bike over Bluetooth Low Energy and
covers the parts of the bike that a riding or testing application needs.
That includes identity, sensor status, rider telemetry, configuration reads,
configuration writes, calibration and the virtual drivetrain.

The library does not implement firmware updates or factory reset. It does not
interpret FTMS control point procedures; `openstage.wftnp` forwards them to the
bike unchanged. It also does not try to be a training application; it gives you
typed records and leaves the presentation to you.

## Requirements

- Python 3.11 or newer.
- A Bluetooth Low Energy adapter supported by [bleak](https://bleak.readthedocs.io/).
- A powered and awake Stages SB20.

The bike accepts one controller on its control service. Close other apps that
hold that connection before you connect, or the bike will not accept this
client.

## Install

```sh
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
```

For a normal user install:

```sh
pipx install .
```

The package installs four commands:

| Command | Purpose |
|---|---|
| `openstage-probe` | Read-only probe and capture writer. |
| `openstage-write` | Explicit configuration writes. |
| `openstage-tui` | Terminal dashboard. |
| `openstage-wftnp` | WFTNP endpoint for a cycling app. |

## First connection

```python
import asyncio

from openstage import SB20Client


async def main() -> None:
    async with SB20Client() as bike:
        print(bike.identity.name, bike.identity.address)
        status = await bike.get_bike_status()
        print(f"flywheel {status.flywheel_rpm:.2f} rpm")


asyncio.run(main())
```

Discovery scans until it finds an SB20-shaped advertisement. Pass `address=` to
skip the scan, or `name_filter=` to restrict advertised names.

## Read telemetry

`SB20Client` emits one `Notification` per characteristic value. `BikeState`
folds those notifications into display-ready values and keeps the source of
each metric.

```python
import asyncio

from openstage import BikeState, SB20Client


async def main() -> None:
    state = BikeState()
    async with SB20Client() as bike:
        await bike.open_telemetry()
        async for note in bike.notifications():
            state.apply(note)
            if state.power_w.value is not None:
                print(f"{state.power_w.value} W from {state.power_w.source}")


asyncio.run(main())
```

Power, cadence, balance and left/right difference default to the Stages
control service. Speed and distance come from FTMS because the control service
does not report them. `BikeState.preferences` selects another source.

## Change configuration

Configuration setters only record what you want. `commit()` reads the current
value, sends only the operations that change something, and optionally stores
the settings record in flash.

```python
import asyncio

from openstage import ButtonAction, SB20Client


async def main() -> None:
    async with SB20Client() as bike:
        result = await (
            bike.settings()
            .set_weight(85.0, 7.5)
            .set_brake(False)
            .set_button_slots({1: ButtonAction.REAR_UP})
            .commit(store=True)
        )
        print(result.writes, result.persisted)


asyncio.run(main())
```

A configuration write updates RAM. It survives a reconnect and disappears after
a power cycle unless it is stored. The gearing map has its own apply and store
steps; see [the drivetrain page](api/gearing.md).

## Record and replay

```sh
openstage-probe --capture session.json
openstage-probe --seconds 60 --capture ride.json
openstage-probe --replay session.json --json
openstage-tui --replay session.json
```

A capture is plain JSON with the raw characteristic values and the query
exchanges. Replay feeds the same typed notifications as a live connection, so
the dashboard and `BikeState` work without a radio.

## Documentation map

| Page | Contents |
|---|---|
| [API reference](api/index.md) | All public modules, classes and functions. |
| [Client](api/client.md) | `SB20Client`, queries, writes and errors. |
| [Settings](api/settings.md) | Fluent configuration writes and persistence. |
| [Drivetrain](api/gearing.md) | Dream Drive, cassette models and flash presets. |
| [Records](api/records.md) | Typed wire records and PDU builders. |
| [State](api/state.md) | `BikeState` and source selection. |
| [Transport](api/transport.md) | BLE boundary, discovery and bleak. |
| [Capture and replay](api/capture.md) | JSON session format and offline replay. |
| [WFTNP endpoint](api/wftnp.md) | Serve the bike to a cycling app over TCP and mDNS. |
| [Command line](cli.md) | `openstage-probe`, `openstage-write`, `openstage-tui`. |
| [Bike notes](bike-notes.md) | Behaviour and limits worth knowing before you write. |
| [Control and telemetry service](stages-sb20-control-telemetry-service.md) | Wire specification for the control service. |
| [Button and brake service](stages-sb20-button-brake-service.md) | Wire specification for the input service. |

## Safety

Every read request passes an exact-value allow-list, and every write passes a
structural allow-list before it reaches the radio. The library has no API for
factory control or firmware update. A write that is not in the allow-list raises
`SafetyError`.

Configuration writes are explicit method calls. Nothing is written during
connection, querying or telemetry.

## License

MIT.
