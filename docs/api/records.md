# Records and builders

`openstage.pdu` is the only module that knows wire offsets. It decodes
characteristic values into frozen dataclasses and builds the requests the
client sends. The client never parses a byte on its own.

## Decoding

### `decode_pdu(data: bytes) -> Pdu`

Decodes one control-service value. A request, a response and an event all use
the same operation byte, so this function returns whichever typed record the
payload matches. An unknown operation or a short payload becomes `UnknownPdu`
with the raw value preserved.

### `decode_notification(stream: Stream, data: bytes) -> Pdu`

Decodes a value according to the characteristic it came from. This is the
function the client calls for every notification.

### `decode_ftms_indoor_bike_data(data: bytes) -> FtmsIndoorBikeData`

Decodes the SB20's non-standard `0x2AD2` frames. Three shapes are recognised.
They are speed only, distance only, and cadence with power. Other shapes retain
their flags and raw bytes.

### `decode_csc(data: bytes) -> CscMeasurement`

Decodes a Cycling Speed and Cadence measurement. The library uses the crank
data to derive cadence when the other streams are unavailable.

### `decode_button_frame(data: bytes) -> ButtonFrame`

Decodes a Button Active value.

### `decode_brake_frame(data: bytes) -> BrakeFrame`

Decodes a Brake Value frame. The first two `uint16` values are the left and
right lever positions; any remaining values are kept in `extra`.

## Base record

### `Pdu`

```python
Pdu(raw: bytes, op: int)
```

| Member | Meaning |
|---|---|
| `raw` | Complete characteristic value. |
| `op` | Operation code, or `-1` for an unknown stream value. |
| `operation` | `Op` member when `op` is known, otherwise `None`. |

All records below are frozen dataclasses derived from `Pdu`.

## Control-service records

| Record | Fields | Notes |
|---|---|---|
| `UnknownPdu` | `payload` | Operation could not be decoded. |
| `SctsIdentity` | `identifier`, `version` | Both may be `None` in a short response. |
| `Power` | `power_w` | Watts, decoded from 0.5 W units. |
| `Weight` | `user_kg`, `equipment_kg` | Kilograms, decoded from 0.01 kg units. |
| `Resistance` | `level` | Raw 0…255. `percent` returns `level / 2.55`. |
| `GradientScaling` | `factor`, `delay_enabled` | `factor` is in 0.5 % units. `percent` returns `factor * 0.5`. |
| `ZeroReset` | `command`, `status`, `valid_mask`, `left_offset`, `right_offset`, `manufacturer_code`, `company_id`, `torque_offset` | `successful` is true for `ZeroResetStatus.SUCCESS`. Fields whose mask bit is clear are `None`. |
| `Battery` | `source`, `percent` | `percent` is `None` for a negative reading. |
| `PowerData` | `mask`, `instantaneous_w`, `accumulated_w`, `cadence_rpm`, `distribution_pct`, `lr_difference` | A clear mask bit leaves the field as `None`. |
| `PowerDataAck` | `command` | Reply to a power-data subscribe or unsubscribe. |
| `PowerMeterPairing` | `command`, `crank_length_mm`, `left_ant_id`, `left_serial`, `right_ant_id`, `right_serial` | A missing trailing field becomes `None`. |
| `ButtonAssignments` | `command`, `values` | `values` is the 15-value wire array. `by_slot` maps wire slots to values. |
| `GearNotification` | `model`, `front_index`, `rear_index`, `front_teeth`, `rear_teeth`, `dream_drive`, `dream_drive_step` | For `COEFFICIENTS`, `coefficient` returns the selected coefficient from `front_teeth`. |
| `GearModelHeader` | `command`, `model`, `front_count`, `rear_count`, `crc8`, `dream_drive`, `dream_drive_step`, `map_size` | Stored-model header. |
| `GearTransferAck` | `command`, `payload` | Gearing acknowledgement without a header. |
| `GearSubscriptionAck` | `command` | Reply to a gear subscribe, unsubscribe or get. |
| `GearMapFragment` | `data` | Map payload fragment. |
| `GearManualTargetPower` | — | Advisory event for a manually selected ERG target. |
| `SensorFirmware` | `command`, `version`, `ant_id`, `manufacturer`, `major`, `minor`, `patch` | The verbose form fills `version` and `ant_id`; the binary form fills the numeric fields. |
| `BikeStatus` | `flywheel_rpm`, `hrm`, `power_meter`, `pairing` | Flywheel speed is decoded from quarter-rpm units. |
| `FtmsParameters` | `grade_percent` | Simulation grade in percent. |
| `Parameters` | `brake_raw`, `dual_ble_raw` | `brake_enabled` and `dual_ble_enabled` convert the tri-state bytes to `bool` or `None`. |
| `StoreSettings` | `command` | `status` returns a `StoreSettingsCommand`; `stored` is true for `STORED` or `STORED_AND_APPLIED`. |
| `PermissionDenied` | — | Reserved `0xFA`. |
| `AlreadyConnected` | `controller_address` | Reserved `0xFB`; payload decoded as text. |
| `ErrorPdu` | `failed_op`, `code` | `error` returns an `ErrorCode` when known. |

## Input-service records

| Record | Fields | Notes |
|---|---|---|
| `ButtonFrame` | `selector`, `header`, `values` | `mask` returns the first 16-bit field. |
| `BrakeFrame` | `left`, `right`, `extra` | Lever positions and any additional values. |

## FTMS and CSC records

### `FtmsIndoorBikeData`

```python
FtmsIndoorBikeData(
    raw, op, flags,
    speed_kph=None,
    distance_m=None,
    cadence_rpm=None,
    power_w=None,
    average_power_w=None,
)
```

Only the fields present in the recognised shape are filled.

### `CscMeasurement`

```python
CscMeasurement(
    raw, op, flags,
    wheel_revs=None,
    wheel_event_time_s=None,
    crank_revs=None,
    crank_event_time_s=None,
)
```

## Enums

### `Stream`

| Member | Value |
|---|---|
| `API` | `"api"` |
| `BUTTONS` | `"buttons"` |
| `BRAKE` | `"brake"` |
| `FTMS_DATA` | `"ftms_data"` |
| `FTMS_STATUS` | `"ftms_status"` |
| `FTMS_CONTROL` | `"ftms_control"` |
| `CSC` | `"csc"` |

### `Op`

Operation codes for the control service:

| Member | Value | Member | Value |
|---|---|---|---|
| `SCTS_ID` | `0x00` | `BUTTONS` | `0x0B` |
| `WIRELESS_SENSOR` | `0x01` | `GEARING` | `0x0C` |
| `POWER` | `0x02` | `SENSOR_FIRMWARE` | `0x0D` |
| `WEIGHT` | `0x03` | `BIKE_STATUS` | `0x0E` |
| `RESISTANCE` | `0x04` | `FTMS_PARAMETERS` | `0x0F` |
| `GRADIENT_SCALING` | `0x05` | `PARAMETERS` | `0x10` |
| `ZERO_RESET` | `0x06` | `PERMISSION_DENIED` | `0xFA` |
| `BATTERY` | `0x07` | `ALREADY_CONNECTED` | `0xFB` |
| `POWER_DATA` | `0x08` | `FACTORY_CONTROL` | `0xFC` |
| `ANT_SCANNER` | `0x09` | `STORE_SETTINGS` | `0xFD` |
| `POWER_METER_PAIRING` | `0x0A` | `ERROR` | `0xFE` |
| | | `LONG_MESSAGE` | `0xFF` |

### Command enums

| Enum | Members |
|---|---|
| `PowerDataCommand` | `SUBSCRIBE=0`, `UNSUBSCRIBE=1`, `EVENT=2` |
| `PairingCommand` | `GET_STATUS=0`, `DEVICES_ARE_PAIRED=1`, `DEVICE_IS_NOT_PAIRED=2`, `RESTART_PAIRING=3`, `IN_PROGRESS=4`, `FAILED=5` |
| `BatterySource` | `POWER_METER=0`, `HEART_RATE=1` |
| `GearingSubOp` | `SUBSCRIPTION=0`, `NOTIFICATION=1`, `TRANSFER=2`, `FRAGMENT=3`, `MANUAL_TARGET_POWER=4` |
| `GearingSubscriptionCommand` | `UNSUBSCRIBE=0`, `SUBSCRIBE=1`, `GET=2` |
| `GearingTransferCommand` | `REQUEST_TRANSFER=0`, `REQUEST_TRANSFER_SUCCESS=1`, `REQUEST_TRANSFER_ERROR=2`, `STORE=3`, `STORE_SUCCESS=4`, `STORE_INVALID_CRC=5`, `STORE_INTERNAL_ERROR=6`, `GET_CURRENT_MODEL=7`, `GET_CURRENT_MODEL_HEADER=8`, `GET_CURRENT_MODEL_SUCCESS=9`, `STORE_IN_PROGRESS=10`, `APPLY=11`, `APPLY_SUCCESS=12` |
| `FirmwareCommand` | `SUBSCRIBE=0`, `UNSUBSCRIBE=1`, `GET_LEFT=2`, `NO_VERSION=3`, `GET_RIGHT=4`, `VERSION=10` |
| `ParametersCommand` | `SET=0`, `GET=1` |
| `StoreSettingsCommand` | `STORE=0`, `IN_PROGRESS=1`, `ERROR=2`, `STORED=3`, `STORED_AND_APPLIED=4` |
| `ZeroResetStatus` | `UNRECOGNIZED=-1`, `INITIATED=0`, `SUCCESS=1`, `ERROR_NO_CONNECTION=2`, `ERROR_TIMEOUT=3`, `ERROR_REQUEST=4`, `ERROR_INVALID_CRANK_POSITION=5`, `MANUFACTURER_CODE=6` |
| `ErrorCode` | `INVALID_ARGUMENTS=0`, `INVALID_STATE=1` |
| `SensorStatus` | `UNRECOGNIZED=-1`, `DISCONNECTED=0`, `SEARCHING=1`, `CONNECTED=2`, `CONNECTED_LOW_BATTERY=3` |
| `PairingStatus` | `UNRECOGNIZED=-1`, `UNKNOWN=0`, `PAIRED=1`, `NOT_PAIRED=2` |
| `ModelType` | `UNRECOGNIZED=-1`, `FRONT_REAR_U8=0`, `FRONT_REAR_U16=1`, `COEFFICIENTS=2` |
| `ButtonAction` | `FRONT_UP=1`, `FRONT_DOWN=2`, `REAR_UP=3`, `REAR_DOWN=4`, `NOT_ASSIGNED=5` |

### `parse_button_action(value: str) -> ButtonAction`

Accepts the canonical names, hyphenated forms, the short `front+`, `rear-`
forms, and the `chainring_*` and `cassette_*` aliases. It also accepts
`external` and `none` as `NOT_ASSIGNED`.

```python
parse_button_action("rear_up")       # ButtonAction.REAR_UP
parse_button_action("cassette-")     # ButtonAction.REAR_DOWN
```

## Read requests

`ReadRequest(name, pdu, label)` describes one allow-listed query.

| Constant | Name | Bytes |
|---|---|---|
| `SCTS_IDENTITY` | `scts_identity` | `00 00` |
| `BIKE_STATUS` | `bike_status` | `0E 00` |
| `POWER_METER_PAIRING` | `power_meter_pairing` | `0A 00` |
| `PARAMETERS` | `parameters` | `10 01` |
| `RESISTANCE` | `resistance` | `04 00` |
| `GRADIENT_SCALING` | `gradient_scaling` | `05 00` |
| `WEIGHT` | `weight` | `03 00` |
| `BATTERY` | `battery` | `07 00` |
| `BUTTONS` | `buttons` | `0B 01` |
| `CURRENT_GEAR` | `current_gear` | `0C 00 02` |
| `GEAR_MODEL` | `gear_model` | `0C 02 07` |
| `SENSOR_FIRMWARE_LEFT` | `sensor_firmware_left` | `0D 02` |
| `SENSOR_FIRMWARE_RIGHT` | `sensor_firmware_right` | `0D 04` |
| `SUBSCRIBE_POWER_DATA` | `subscribe_power_data` | `08 00` |
| `UNSUBSCRIBE_POWER_DATA` | `unsubscribe_power_data` | `08 01` |
| `SUBSCRIBE_GEARING` | `subscribe_gearing` | `0C 00 01` |
| `UNSUBSCRIBE_GEARING` | `unsubscribe_gearing` | `0C 00 00` |

`QUERY_REQUESTS` is the tuple used by `SB20Client.query_all()`. It contains the
first twelve read requests and excludes the identity handshake and the
subscriptions.

`READ_ONLY_REQUESTS` is the exact-value allow-list used by
`ensure_read_only()`. It maps each request value to its label.

## Write requests

`WriteRequest(name, pdu, label)` describes one configuration write.
`STORE_SETTINGS_WRITE` is the settings store (`FD 00`); `ZERO_RESET_WRITE` is
the calibration request (`06 00`).

### Setters

| Function | Produces |
|---|---|
| `build_set_weight(user_kg, equipment_kg)` | `03 01`, both weights in 0.01 kg units. |
| `build_set_parameters(brake_enabled, dual_ble_enabled)` | `10 00`; `None` encodes that field as unchanged. |
| `build_set_gradient_scaling(factor, delay_enabled)` | `05 01`, factor in 0.5 % units. |
| `build_set_gradient_scaling_percent(percent, delay_enabled)` | Same, with percent input. |
| `build_set_resistance(level)` | `04 01`, raw level 0…255. |
| `build_set_resistance_percent(percent)` | Same, with percent input. |
| `build_set_crank_length(mm)` | `0A 03`, length in 0.5 mm units. |
| `build_set_target_power(watts)` | `02 00`, target in 0.5 W units plus four zero octets. |
| `build_set_grade(percent)` | `0F 00`, grade in 0.001 % units plus four zero octets. |
| `build_set_buttons(values)` | `0B 00`, exactly 15 wire values. |
| `build_set_button_slots(values)` | 14 physical values, plus a zero byte. |
| `build_set_button_actions(actions)` | 14 `ButtonAction` values; the final byte is zero. |

### Button helpers

| Function | Meaning |
|---|---|
| `apply_physical_button_actions(current_wire_values, changes)` | Change physical buttons 1…14 and preserve the rest. |
| `button_actions_from_wire(values)` | Map the first 14 wire values to `ButtonAction` or `None`. |
| `BUTTON_WIRE_SLOTS` | Wire order is 1, 2, 3, 6, 7, 8 and 11 through 19. |
| `BUTTON_MASK_SLOTS` | The first 14 wire slots; these have button-active mask bits. |
| `CRANK_LENGTHS_MM` | `(165.0, 170.0, 172.5, 175.0)`. |

### Safety gates

#### `ensure_read_only(pdu: bytes) -> str`

Returns the allow-list label for an exact read request. Raises `SafetyError`
for anything else.

#### `ensure_write_allowed(pdu: bytes) -> str`

Validates one configuration PDU by operation, command, length and value range.
It returns a description of the write. Factory control, firmware update and
every operation outside the approved slice raise `SafetyError`.

The gate accepts `0C 02 0B` with the full transfer header, which is how APPLY is
sent. It also validates the header's model, counts, CRC range, map size and
Dream Drive step for the request and apply commands.

#### `ensure_ftms_control(pdu: bytes) -> str`

Validates one Fitness Machine Control Point write: the op code must be one the
FTMS specification defines (Table 4.15) and carry at least its parameter bytes.
The bridge uses it so every FTMS procedure the bike implements stays reachable
while non-FTMS bytes never reach the radio.

## Gearing helpers

| Function | Meaning |
|---|---|
| `crc8(data)` | CRC-8 with polynomial `0x31`, init `0xFF`, MSB first. |
| `sorted_front_gears(values)` | Validate and sort smallest first. |
| `sorted_cassette_gears(values)` | Validate and sort largest first. |
| `dream_drive_coefficients(count)` | Coefficient table for 10…50 gears. |
| `build_dream_drive_map(count)` | Little-endian `uint16` coefficient map. |
| `build_front_rear_map(front, cassette)` | Model 0 map: front teeth then cogs. |
| `build_preset_index(front_sets, cassettes)` | Fingerprint index for preset recovery. |
| `build_gearing_header(...)` | Transfer request PDU. |
| `split_gearing_map(map_bytes)` | Split a map into fragments of at most 18 bytes. |
| `build_gearing_fragment(data)` | One `0C 03` fragment. |
| `build_gearing_apply(header)` | APPLY with the full transfer header. |
| `build_gearing_store()` | `0C 02 03`. |

`build_gearing_apply()` requires an 11-octet transfer header: the two operation
octets, the command, model, front count, rear count, CRC, Dream Drive flag, step
and two map-size octets. It replaces the command with `APPLY` and keeps the
rest.

## Constants

| Constant | Value |
|---|---|
| `DREAM_DRIVE_GEARS_MIN` | 10 |
| `DREAM_DRIVE_GEARS_MAX` | 50 |
| `DREAM_DRIVE_STEP_MIN` | 0 |
| `DREAM_DRIVE_STEP_MAX` | 9 |
| `GEARING_FRAGMENT_MAX` | 18 |
| `CRANK_LENGTHS_MM` | 165.0, 170.0, 172.5, 175.0 |

## Example: decode a stored header

```python
from openstage import pdu

header = pdu.decode_pdu(bytes.fromhex("0c020800010c0f00040d00"))
if isinstance(header, pdu.GearModelHeader):
    print(header.model, header.front_count, header.rear_count)
    print(f"CRC 0x{header.crc8:02X}, map {header.map_size} B")
```
