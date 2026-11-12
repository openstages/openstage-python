# WFTNP endpoint

`openstage.wftnp` serves one Stages SB20 to local cycling applications over the
Wahoo Fitness TNP tunnel: mDNS discovery plus a TCP connection that carries BLE
GATT operations. The bike stays on Bluetooth; the application sees a device it
can discover, read and control.

The package is optional. Install it with the extra:

```sh
pip install 'openstage[wftnp]'
```

## Run a bridge

The package ships a command for the common case:

```sh
openstage-wftnp --address AA:BB:CC:DD:EE:FF
```

It serves until SIGINT or SIGTERM, then closes the Bluetooth link. See
[the command line page](../cli.md) for the options.

The dashboard starts the same endpoint while it runs and drives it from its own
notification loop. It uses the standard port so an app that remembers the
endpoint keeps working, and falls back to an ephemeral port only when another
bridge already holds it. Pass `--no-wftnp` to keep the dashboard local to the
host.

From code:

```python
import asyncio

from openstage import SB20Client
from openstage.wftnp import WftnpBridge


async def main() -> None:
    async with SB20Client() as bike:
        bridge = WftnpBridge(
            bike,
            name="Square Bridge",
            txt={
                "serial-number": "SQ-0001",
                "mac-address": "02:00:00:00:00:01",
                "ble-service-uuids": "347b0001-7635-408b-8918-8ff3949ce592",
            },
        )
        await bridge.start()
        await asyncio.Event().wait()


asyncio.run(main())
```

The advertised name must contain `square`, and `ble-service-uuids` must list the
Elite service, or the client will not treat the endpoint as an Elite Square.

`start()` first puts the bike's RAM drivetrain back in sync with flash, then
opens the TCP listener, publishes the mDNS record and begins mirroring bike
notifications. The sync is a configuration write, the same one the dashboard
performs on connect: a header read reports flash but does not prove the active
RAM map, and mismatched maps would give the app the wrong gear counts.
`stop()` cancels the mirror and closes both.

## Services

| Service | Characteristics | Behaviour |
|---|---|---|
| Elite Square (`347b0001-7635-408b-8918-8ff3949ce592`) | Status `347b0043` and Buttons `347b0045`, read and notify | Gear, battery, pressed buttons and twenty rolling button counters. |
| Fitness Machine (`00001826-…`) | Feature, Indoor Bike Data, Status, Control Point | Proxy to the bike; see below. |
| Stages control (`0c46beaf-…`) | Request write, Response notify | Weight only; see below. |

## FTMS is a proxy

Indoor Bike Data and Status values are forwarded from the bike unchanged, and a
Control Point write is passed to the bike as it arrives. The bike's answer comes
back as a notification on the same characteristic. `openstage.pdu.ensure_ftms_control()`
checks that the write is a defined FTMS op code with at least its parameter
bytes, and nothing else is inspected.

This keeps every procedure the bike implements reachable, including the ones
this library has no typed wrapper for.

## Weight is the only Stages operation

The Stages control service accepts exactly one request: the rider and equipment
weight write (`03 01`, two little-endian hundredths of a kilogram). The bridge
forwards it through `SB20Client.set_weight()` and returns the bike's own reply on
the response characteristic. Any other write on that service is rejected.

The weight change lives in RAM, which matches the bike: it survives a reconnect
and disappears after a power cycle.

## Concurrency and rate

Every outgoing Bluetooth operation is serialised by `SB20Client`, so one request
is in flight at a time and the rest queue behind it. That covers app writes and
the bridge's own configuration calls.

Notifications are sent only when a mirrored value changes. The bike repeats
button and brake frames while a control is held; those repeats produce no
change and are dropped, which keeps the Elite stream under its ten-per-second
budget.

## Limits

- One client at a time. A second TCP connection replaces the first.
- Only the three services above are exposed. The FTMS service is the bike's own,
  so nothing simulates trainer resistance.
- The Elite buttons map by physical position. Buttons 1-2 are the left
  shifters, 3 the left action key, 4-5 the right shifters, 6 the right action
  key, and the rest are 1, 2, 3, 4 and 11-14. The app remaps them itself, so
  the numbers stay stable. See `elite_button()`.
- The tested SB20 reports ten physical buttons; slots 11-14 stay mapped but
  produced no frames on that unit.
