# Settings and persistence

Configuration writes use a small fluent builder. Setters record intent only.
`commit()` reads the current bike values, compares them with the intent, and
sends only the operations that change something.

```python
import asyncio

from openstage import ButtonAction, SB20Client


async def main() -> None:
    async with SB20Client() as bike:
        commit = await (
            bike.settings()
            .set_weight(85.0, 7.5)
            .set_brake(False)
            .set_button_slots({1: ButtonAction.REAR_UP})
            .commit(store=True)
        )
        print(commit.writes, commit.persisted)


asyncio.run(main())
```

## `BikeSettings`

Create one with `SB20Client.settings()`. A builder belongs to one client and one
commit. `commit()` clears the recorded intent, so a second commit without new
setters sends nothing.

### Setter methods

| Method | Intent |
|---|---|
| `set_weight(user_kg, equipment_kg)` | Set both weights. |
| `set_brake(enabled)` | Set the flywheel brake flag. |
| `set_parameters(brake_enabled, dual_ble_enabled)` | Set either parameter; `None` leaves that field alone. |
| `set_gradient_scaling(factor, delay_enabled=False)` | Set the gradient scaling factor and delay flag. |
| `set_crank_length(mm)` | Set crank length; this restarts power-meter pairing. |
| `set_button_actions(actions)` | Replace all 14 physical assignments. |
| `set_button_slots(changes)` | Change selected physical buttons 1…14. |
| `set_buttons(values)` | Replace all 15 raw wire values. |

The button setters interact. `set_button_actions()` and `set_buttons()` replace
the whole set, while `set_button_slots()` accumulates changes on top of the
current read-back. Calling a whole-set setter clears earlier slot changes and
the other whole-set intent.

### `await commit(*, store=False) -> SettingsCommit`

For each category with recorded intent, `commit()`:

1. Reads the matching value from the bike.
2. Compares it with the requested value.
3. Sends the Set only when the value differs.

Weight comparison uses 0.005 kg. Gradient scaling compares the factor and the
delay flag. Crank length compares within 0.01 mm. Buttons compare the complete
15-value array.

When `store=True` and at least one Set was sent, the settings record is written
to flash with `0xFD/0`. A store is not sent when nothing changed. A store that
does not reach a terminal reply raises `BikeTimeout`.

## Result types

### `SettingsCommit`

```python
SettingsCommit(operations: tuple[WriteResult, ...], store: StoreResult | None = None)
```

| Member | Meaning |
|---|---|
| `operations` | One `WriteResult` per Set that was actually sent. |
| `store` | The store result, or `None`. |
| `writes` | Number of Sets sent. |
| `persisted` | True when the store exists and reports stored. |

### `WriteResult`

```python
WriteResult(set_response: pdu.Pdu, store: StoreResult | None = None)
```

`set_response` is the typed response to the Set. `persisted` is true only when
`store` exists and reports `stored`.

### `StoreResult`

```python
StoreResult(messages: tuple[StoreSettings, ...])
```

| Member | Meaning |
|---|---|
| `messages` | Every `FD` message observed for this store. |
| `command` | The last `StoreSettingsCommand`. |
| `stored` | True for `STORED` or `STORED_AND_APPLIED`. |
| `busy` | True when the bike answered `ERROR`. |

## RAM and flash

A Set updates RAM. The value survives a BLE disconnect, and it does not survive
a power cycle. The settings store (`0xFD/0`) writes the settings record to
flash. The record covers weight, button assignments, gradient scaling, crank
length, sensor configuration, brake and dual-BLE. It does not cover the
drivetrain map, which has its own store command.

The bike emits `FD 04` (stored and applied) after a configuration Set even
without a store request. Do not treat `FD 04` as proof of persistence. A store
request starts with `FD 01`, and `FD 03` is the stored confirmation. Repeating a
store with an unchanged record performs no flash write.

The low-level write methods accept `store=True` and behave the same way. They
return `WriteResult` directly.

```python
result = await bike.set_weight(85.0, 7.5, store=True)
if result.persisted:
    print("stored")
```
