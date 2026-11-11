# Drivetrain configuration

The SB20 stores a virtual drivetrain as a model plus a map. The library supports
the two models the bike accepts: Dream Drive and a front-ring/cassette map.

Applying a map installs it in RAM. Storing writes the applied map to flash.
These are separate operations because a flash store can take a long time and
gets slower with repeated stores in one power cycle.

## `GearSettings`

Create one with `SB20Client.gears()`.

```python
import asyncio

from openstage import DrivetrainMode, SB20Client


async def main() -> None:
    async with SB20Client() as bike:
        result = await (
            bike.gears()
            .set_mode(DrivetrainMode.DREAM_DRIVE)
            .set_gears_number(24)
            .set_front_shift(2)
            .commit()
        )
        print(result.plan.map_size, result.plan.crc8)


asyncio.run(main())
```

### Setter methods

| Method | Meaning |
|---|---|
| `set_mode(mode)` | `DrivetrainMode.DREAM_DRIVE` or `DrivetrainMode.SIMULATED_CASSETTE`. |
| `set_gears_number(count)` | Dream Drive gear count, 10…50. |
| `set_front_shift(step)` | Dream Drive big shift, 0…9. A front-direction shift changes the rear index by this step. |
| `set_front_gears(values)` | Front ring teeth. The list is sorted smallest to largest. |
| `set_cassette_gears(values)` | Cassette teeth. The list is sorted largest to smallest. |

The two modes use different setters:

| Mode | Required setters | Map |
|---|---|---|
| Dream Drive | `set_gears_number()`, optional `set_front_shift()` | Model type 2, one `uint16` coefficient per gear. |
| Simulated cassette | `set_front_gears()`, `set_cassette_gears()` | Model type 0, one `uint8` per sprocket: front rings then cogs. |

### `plan() -> GearPlan`

Builds the map, CRC and PDUs without touching the bike. Use this to preview a
configuration or to validate input.

Raises `ValueError` when a required setter is missing or a value is outside the
limit.

### `await commit() -> GearTransferResult`

1. Sends simulation grade `0.0` so ERG does not suppress shifting.
2. Sends the transfer request and waits for `REQUEST_TRANSFER_SUCCESS`.
3. Sends the map in fragments of at most 18 bytes.
4. Sends APPLY and waits for `APPLY_SUCCESS`.

APPLY carries the full transfer header again. The header contains the model,
front count, rear count, CRC, Dream Drive flag, step and map size, and only the
command octet changes to `0x0B`. The three-byte `0C 02 0B` form is not accepted
by the bike.

`commit()` is RAM-only. Call `SB20Client.store_gearing()` to persist the applied
map.

## `GearPlan`

A frozen record of a fully encoded transfer, produced by `plan()`.

| Field | Type | Meaning |
|---|---|---|
| `mode` | `DrivetrainMode` | Selected mode. |
| `model` | `ModelType` | Wire model type: `FRONT_REAR_U8` or `COEFFICIENTS`. |
| `front_count` | `int` | Number of front rings. |
| `rear_count` | `int` | Number of cogs, or Dream Drive gears. |
| `dream_drive` | `bool` | Dream Drive flag. |
| `step` | `int` | Big-shift step. Cassette maps use 4, the value the bike reports for that model. |
| `front_gears` | `tuple[int, ...]` | Front teeth in bike order. Dream Drive leaves this empty. |
| `cassette_gears` | `tuple[int, ...]` | Cassette teeth in bike order. Dream Drive leaves this empty too. |
| `coefficients` | `tuple[int, ...]` | Dream Drive coefficients; a model 0 map leaves this empty. |
| `map_bytes` | `bytes` | Map payload. |
| `crc8` | `int` | CRC-8 over `map_bytes`. |
| `header` | `WriteRequest` | Transfer request PDU. |
| `fragments` | `tuple[WriteRequest, ...]` | Fragment PDUs. |
| `apply` | `WriteRequest` | APPLY PDU with the full header. |

Convenience members:

| Member | Meaning |
|---|---|
| `map_size` | `len(map_bytes)`. |
| `total_gears` | Dream Drive rear count, otherwise front count × rear count. |
| `header_pdu` | Raw transfer header bytes. |
| `as_header()` | A synthetic `GearModelHeader` for `BikeState` when readback is unavailable. |
| `matches(header)` | True when a header describes the same model, counts, CRC, flag, step and size. |
| `matches_gear(gear)` | True when a gear notification belongs to this map. |

`matches()` ignores the step octet for model 0 because the bike reports step 4
there without using it for shifting.

## Results

### `GearTransferResult`

```python
GearTransferResult(
    plan: GearPlan,
    request_ack: pdu.GearTransferAck,
    apply_ack: pdu.GearTransferAck,
)
```

`plan` is the map that was sent. The two acknowledgements are the bike's replies
to the request and to APPLY.

### `GearStoreResult`

```python
GearStoreResult(
    ack: pdu.GearTransferAck | None,
    error: str | None,
    header_after: pdu.GearModelHeader | None = None,
)
```

| Member | Meaning |
|---|---|
| `ack` | The terminal store acknowledgement, or `None` on timeout. |
| `error` | A description when the store failed or was not confirmed. |
| `header_after` | The stored header re-read after success. |
| `stored` | True when `ack.command` is `STORE_SUCCESS`. |

## `SB20Client.store_gearing()`

Sends `0C 02 03` and waits for a terminal store reply: `STORE_SUCCESS`,
`STORE_INVALID_CRC` or `STORE_INTERNAL_ERROR`. `STORE_IN_PROGRESS` (`0x0A`) is
not terminal; it means the request is queued.

The request lock is held for the whole wait, so no other command reaches the
bike while flash may be writing. A flash read during the write can disturb it.
On success the stored header is read back with `0C 02 07`; a failed or
unconfirmed store does not read flash.

The applied map stays in the bike's RAM buffer until a header read clears it, so
a store can follow APPLY, even after a reconnect in the same power cycle. If a
`0C 02 07` read happens between APPLY and store, the bike answers
`STORE_INTERNAL_ERROR`; apply the map again before storing.

## Stored-preset recovery

The bike returns a stored header reliably, but its map readback can be
incomplete. The header carries the map CRC, and the first fragment carries the
start of the map. `settings_for_stored_header()` uses those values and the
bundled tables to rebuild the `GearSettings` that produced a stored map.

```python
from openstage.gearing_data import settings_for_stored_header

settings = settings_for_stored_header(bike, header, fragments, gear)
if settings is not None:
    await settings.commit()
```

The return value is `None` when the header is not in the bundled data or when
more than one preset matches. A returned `GearSettings` has not been committed.

### `GearSet`

```python
GearSet(name: str, gears: tuple[int, ...], manufacturer: str = "")
```

A named front-ring or cassette set from the package data.

### `load_front_rings() -> tuple[GearSet, ...]`

Loads the bundled front-ring table.

### `load_cassettes() -> tuple[GearSet, ...]`

Loads the bundled cassette table, sorted for display.

### `build_preset_index() -> dict[str, list[str]]`

Builds the fingerprint index from the bundled tables. The key is
`<front_count>x<rear_count>:<crc8>`. A value is a list of map payloads in hex.

### `load_preset_index() -> dict[str, tuple[bytes, ...]]`

Loads the precomputed index shipped with the package. The index is generated
by `scripts/update_gearing_data.py`; edit `front_rings.json` or
`cassettes.json` and run that script to rebuild it.

## Limits and CRC

| Item | Limit |
|---|---|
| Dream Drive gears | 10…50 |
| Dream Drive big shift | 0…9 |
| Sprocket counts | 1…255 |
| Map size | 0…65535 bytes |
| Fragment payload | 18 bytes |
| Sprocket values | 1…255, no duplicates |

The map CRC is CRC-8 with polynomial `0x31`, initial value `0xFF`, MSB first,
without reflection or a final XOR. It covers the map payload only, not the
transfer header.

```python
from openstage.pdu import crc8

print(f"0x{crc8(map_bytes):02X}")
```
