# openstage

Async Python library and terminal dashboard for the **Stages SB20** indoor
bike. It speaks Bluetooth Low Energy, decodes the bike's control and input
services, and exposes typed reads, telemetry and explicit configuration writes.

The library covers:

- discovery, connection and Device Information;
- bike status, sensor pairing, battery and firmware versions;
- power, cadence, balance, speed and distance from the control service, FTMS
  and CSC;
- configuration reads and writes for weight, brake, buttons, gradient scaling,
  crank length and settings persistence;
- ERG, simulation grade and resistance control;
- zero-reset calibration;
- Dream Drive and front-ring/cassette drivetrain maps, with separate RAM apply
  and flash store;
- JSON capture and offline replay.

It does not implement firmware updates, factory reset or the FTMS control
point.

## Install

```sh
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
```

Or install it as a command-line tool:

```sh
pipx install .
```

Python 3.11 or newer is required. The bike accepts one controller on its control
service, so close other applications that hold that connection before you
connect.

## Use the library

```python
import asyncio

from openstage import BikeState, SB20Client


async def main() -> None:
    state = BikeState()
    async with SB20Client() as bike:
        print(bike.identity.name, bike.identity.address)
        await bike.open_telemetry()
        async for note in bike.notifications():
            state.apply(note)
            if state.power_w.value is not None:
                print(f"{state.power_w.value} W")


asyncio.run(main())
```

Discovery scans until it finds an SB20-shaped advertisement. Pass `address=` to
skip the scan, or `name_filter=` to restrict advertised names.

Configuration writes use a fluent builder. Setters only record intent;
`commit()` reads the current value, sends only what changed, and optionally
stores the settings record in flash.

```python
result = await (
    bike.settings()
    .set_weight(85.0, 7.5)
    .set_brake(False)
    .commit(store=True)
)
print(result.writes, result.persisted)
```

A Set writes RAM. It survives a reconnect and disappears after a power cycle
unless it is stored. The drivetrain map has its own apply and store steps.

## Command line

```sh
# Read-only probe; writes a capture for replay.
openstage-probe --capture session.json

# Queries plus 60 seconds of telemetry.
openstage-probe --seconds 60 --capture ride.json

# Offline review.
openstage-probe --replay session.json --json
openstage-tui --replay session.json

# Explicit configuration write.
openstage-write weight --user-kg 85.1 --store --yes
```

| Command | Purpose |
|---|---|
| `openstage-probe` | Read-only queries, optional telemetry and capture. |
| `openstage-write` | Explicit configuration writes; requires `--yes`. |
| `openstage-tui` | Terminal dashboard with ERG and drivetrain controls. |

## Documentation

- [Overview and quick start](docs/index.md)
- [API reference](docs/api/index.md)
- [Command line](docs/cli.md)
- [Bike notes](docs/bike-notes.md)
- [Control and telemetry service specification](docs/stages-sb20-control-telemetry-service.md)
- [Button and brake service specification](docs/stages-sb20-button-brake-service.md)

## Safety

Read requests pass an exact-value allow-list. Configuration writes pass a
structural allow-list that checks operation, command, length and value range.
Factory control and firmware update are not implemented. A PDU outside the
allow-list raises `SafetyError` before it reaches the radio.

Nothing is written during discovery, connection, querying or telemetry.

## Development

```sh
.venv/bin/python -m pytest
.venv/bin/mypy openstage
```

The drivetrain preset index is generated from the bundled tables:

```sh
python build.py
```

## License

MIT.
