# Command line

The package installs four commands. The first three share the connection
options `--address`, `--name`, `--scan-timeout` and `--request-timeout`.

```sh
openstage-probe --help
openstage-write --help
openstage-tui --help
openstage-wftnp --help
```

## Connection options

| Option | Meaning |
|---|---|
| `--address ADDRESS` | Connect to this BLE address and skip discovery. |
| `--name TEXT` | During discovery, accept only advertised names containing this text. |
| `--scan-timeout SECONDS` | Seconds per scan attempt; discovery retries until a bike appears. Default 5. |
| `--request-timeout SECONDS` | Seconds to wait for one control-service response. Default 5. |

## `openstage-probe`

The probe is read-only. It runs the allow-listed queries, optionally records
telemetry, and prints a typed summary. It never sends a configuration write.

```sh
openstage-probe --capture session.json
openstage-probe --seconds 60 --capture ride.json
openstage-probe --replay session.json --json
```

| Option | Meaning |
|---|---|
| `--seconds SECONDS` | Record telemetry for this long after the queries. Zero means queries only. |
| `--capture FILE` | Write the session as JSON. |
| `--json` | Print the summary as JSON. |
| `--no-buttons` | Skip the button and brake service. |
| `--no-ftms` | Skip FTMS Indoor Bike Data and Status. |
| `--no-csc` | Skip CSC. |
| `--replay FILE` | Replay a capture instead of using BLE. |
| `--replay-speed FACTOR` | Replay speed multiplier. Default 10. |
| `--loop` | Replay a capture repeatedly. |

The probe prints progress to stderr and the summary to stdout. Exit status is 0
on success, 1 on a library error or when no query is answered, and 130 on
interrupt. Partial query failures are reported individually and exit 0 with a
warning.

## `openstage-write`

The write command reads the baseline, sends one explicit Set, reads the value
back, and optionally stores the settings record in flash.

Every live write requires `--yes`; without it the command exits with status 2.
Use `--json` for machine-readable output.

A settings commit is all-or-nothing from the tool's point of view. If a write
fails after an earlier step succeeded, the command names the failed step and
reports that device state is unknown. A readback failure is reported separately
and also exits nonzero, because the write may already have been applied.

### Common options

| Option | Meaning |
|---|---|
| `--store-timeout SECONDS` | Seconds to wait for a settings store. Default 10. |
| `--zero-timeout SECONDS` | Seconds to wait for zero reset. Default 30. |
| `--json` | Print the result as JSON. |
| `--yes` | Confirm that the write is approved. |

### Commands

#### `weight`

```sh
openstage-write weight --user-kg 85.1 --yes
openstage-write weight --user-kg 85.1 --store --yes
```

| Option | Meaning |
|---|---|
| `--user-kg KG` | New user weight. The current value is kept when omitted. |
| `--equipment-kg KG` | New equipment weight. The current value is kept when omitted. |
| `--store` | Store the settings record after the Set. |

At least one weight is required.

#### `parameters`

```sh
openstage-write parameters --brake false --store --yes
```

| Option | Meaning |
|---|---|
| `--brake true\|false\|unchanged` | Flywheel brake flag. |
| `--dual true\|false\|unchanged` | Dual-BLE flag. |
| `--store` | Store the settings record after the Set. |

At least one option is required.

#### `buttons`

```sh
openstage-write buttons --assign "1=front_up,2=front_down,4=rear_up,5=rear_down" --store --yes
openstage-write buttons --actions "front_up,front_down,not_assigned,rear_up,rear_down,not_assigned,front_up,front_down,rear_down,rear_up,not_assigned,not_assigned,not_assigned,not_assigned" --store --yes
```

| Option | Meaning |
|---|---|
| `--assign PAIRS` | Physical `button=action` pairs. Other buttons keep their current values. |
| `--actions LIST` | 14 actions in physical slot order. |
| `--store` | Store the settings record after the Set. |

Exactly one of `--assign` and `--actions` is required. Action names are
`front_up`, `front_down`, `rear_up`, `rear_down`, `not_assigned` and
`external`.

#### `gradient`

```sh
openstage-write gradient --percent 100 --yes
openstage-write gradient --factor 200 --delay --store --yes
```

| Option | Meaning |
|---|---|
| `--factor VALUE` | Raw factor in 0.5 % units, 0…65535. |
| `--percent VALUE` | Factor as a percentage. Mutually exclusive with `--factor`. |
| `--delay` | Enable the delay flag. |
| `--store` | Store the settings record after the Set. |

#### `resistance`

```sh
openstage-write resistance --percent 50 --yes
openstage-write resistance --level 128 --yes
```

| Option | Meaning |
|---|---|
| `--level VALUE` | Raw level, 0…255. |
| `--percent VALUE` | Level as a percentage, 0…100. Mutually exclusive with `--level`. |

Resistance is a control mode, not a persisted setting.

#### `crank-length`

```sh
openstage-write crank-length --mm 172.5 --store --yes
```

`--mm` accepts 165, 170, 172.5 or 175. Changing the crank length restarts
power-meter pairing.

#### `zero-reset`

```sh
openstage-write zero-reset --yes
```

Starts calibration and prints the status plus any offsets the bike returns.
Position both crank arms vertically first. There is no cancel command.

#### `power`

```sh
openstage-write power --watts 200 --yes
```

Sets the ERG target. This is a control mode, not a persisted setting.

#### `grade`

```sh
openstage-write grade --percent 5 --yes
```

Sets the simulation grade, −20…+20 %.

#### `erg`

```sh
openstage-write erg --watts 200 --yes
openstage-write erg --off --yes
```

`--watts` enters ERG at a target. `--off` leaves ERG by sending simulation grade
zero.

#### `store`

```sh
openstage-write store --yes
```

Sends the settings store without a preceding Set. This is useful when a Set was
made by another tool and you only need to persist the current settings record.

## `openstage-tui`

The dashboard uses the live control path for ERG simulation and configuration,
or a capture for offline review. A live session also serves the WFTNP endpoint,
so a cycling app can ride against the same bike while the dashboard shows what
it sends.

At startup the dashboard verifies its dependencies and preflights the WFTNP
port. Missing dependencies and unusable ports abort with a message. Scanning
for a sleeping bike continues until it is found. A bike drop is retried with
backoff while the WFTNP endpoint stays up; a Bluetooth adapter error aborts.
An incompatible device is retried twice, then reported.

```sh
openstage-tui
openstage-tui --replay session.json
openstage-tui --replay session.json --snapshot 2 --json
```

| Option | Meaning |
|---|---|
| `--replay FILE` | Replay a capture instead of using BLE. |
| `--speed FACTOR` | Replay speed multiplier. Default 1. |
| `--loop` | Loop a replay. |
| `--no-telemetry` | Do not open the reversible telemetry subscriptions. |
| `--no-wftnp` | Do not serve the WFTNP endpoint while the dashboard runs. |
| `--snapshot SECONDS` | Headless mode. Feed for this long, print the screen, then exit. |
| `--json` | With `--snapshot`, also print the state as JSON. |
| `--width` | Snapshot width. Default 110. |
| `--height` | Snapshot height. Default 36. |

### Keys

| Key | Action |
|---|---|
| `q` | Quit. |
| `r` | Re-send all queries. |
| `o` | Open the options pane. |
| `g` or `d` | Open the drivetrain screen. |
| `m` | Show button read-back and frame counters. |
| `l` | Open the scrollable event log. |
| `z` | Open the zero-reset overlay. |
| `e` | Toggle ERG on or off. |
| `+` / `-` | Change the ERG target by 10 W. |
| `c` | Clear the event log. |

### Main screen

The dashboard requires the control service and response characteristic, the
button and brake characteristics, and the FTMS feature, indoor-data, status and
control-point characteristics. A missing required service or characteristic is
an incompatible-device error. CSC is optional: if it is absent or cannot be
enabled, cadence falls back to API/FTMS and the options pane says CSC is
unavailable.

On connect the dashboard selects simulation mode at 0.0 % grade so manual
shifting works. ERG starts at 100 W and is capped at 500 W in the interface.
While ERG is on, handlebar cassette up/down changes the target by 10 W and
chainring up/down changes it by 20 W, using the configured button assignments.
A held button repeats after 0.5 seconds at two presses per second.

### Options pane

`o` edits the power/cadence source, user and equipment weight, flywheel brake,
crank length and the first 10 physical button assignments. The internal wire
gaps for slots 4, 5, 9 and 10 stay hidden.

Edits are committed when the pane closes. **Close & apply** writes only changed
values; **Close without writing** discards them. The flash-store checkbox is off
by default.

### Drivetrain screen

`g` opens the drivetrain screen. It previews the map, CRC and header, then
offers **Apply (RAM)** and **Store to flash**. Apply is instant and RAM-only.
Store is separate and can take a long time; the screen blocks other commands
while it runs. See [Drivetrain](api/gearing.md).

### Zero reset

`z` opens the calibration overlay. Position both crank arms vertically and
press Start. The overlay cannot be closed until the reset succeeds, fails or
times out, because the bike has no cancel command.

### Replay

In replay mode the dashboard disables every write control. It still renders
rider metrics, read-back values and the drivetrain view from the capture.

## `openstage-wftnp`

Serves the bike to a local cycling application over the WFTNP tunnel and
advertises it over mDNS. [The WFTNP page](api/wftnp.md) describes what the
endpoint exposes.

```sh
openstage-wftnp --address AA:BB:CC:DD:EE:FF
```

| Option | Meaning |
|---|---|
| `--name TEXT` | mDNS instance name, not a discovery filter. It must contain `square` for Elite detection. Default `Square Bridge`. |
| `--port PORT` | TCP port to listen on. Default 36866. |
| `--serial TEXT` | `serial-number` TXT value. |
| `--mac TEXT` | `mac-address` TXT value. Keep it stable, because the app keys button bindings by it. |
| `--no-advertise` | Serve TCP without mDNS. |
| `--debug` | Log every WFTNP frame in both directions. |

SIGINT and SIGTERM stop the endpoint and close the Bluetooth link. Killing the
process with an uncatchable signal leaves BlueZ holding the connection, and the
bike refuses the next one until it is disconnected.
