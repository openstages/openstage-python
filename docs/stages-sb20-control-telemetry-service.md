# Stages SB20 control and telemetry service

This document defines the vendor-specific GATT service used to control a Stages SB20 indoor bike and to read its telemetry. It assumes the reader is familiar with Bluetooth GATT: services, characteristics, notifications, write commands, and the Client Characteristic Configuration descriptor (CCCD).

The service has two characteristics: one accepts requests from a host, and the other returns responses and unsolicited events. A host can select a riding mode, set resistance or grade, calibrate power meters, configure sensors, assign buttons, transfer a virtual drivetrain, and request that settings be stored.

## 1. Notation

Multi-octet fields are little-endian. The field types `u8`, `u16`, `u32`, `i16`, and `i32` mean unsigned and signed integers of that width. PDU lengths include the operation and command octets unless stated otherwise.

The word *must* marks a requirement for interoperability. *Should* marks a recommendation. *May* marks an optional behaviour.

## 2. Service declaration

| Item | Value |
|---|---|
| Service UUID | `0c46beaf-9c22-48ff-ae0e-c6eae1a2f4e5` |
| Service type | Primary |
| Advertising | The service is not advertised. The bike advertises the Fitness Machine service `0x1826`, which a host can use for discovery. |

### 2.1 Characteristics

| Characteristic | UUID | Properties | Use |
|---|---|---|---|
| API Request | `0c46beb1-9c22-48ff-ae0e-c6eae1a2f4e5` | Write Without Response | Host to bike |
| API Response | `0c46beb0-9c22-48ff-ae0e-c6eae1a2f4e5` | Notify | Bike to host |

The API Request characteristic accepts requests written as ATT Write Commands. A bike should also accept a Write Request, although the property does not advertise it. The maximum characteristic value length is 20 octets.

The API Response characteristic has a CCCD (`0x2902`). A host enables notifications by writing `0x0001` to the CCCD. The bike sends no response or event before that write.

### 2.2 Transport

| Parameter | Value |
|---|---|
| ATT MTU | 23 octets is sufficient and is the usual value |
| Maximum PDU length | 20 octets |
| PDUs longer than 20 octets | Not defined for this service; use the operation-specific fragmentation described in section 5.13.6 |
| Security | Open access; encryption and bonding are not required |

A larger MTU does not raise the 20-octet limit, because the characteristics declare a maximum length of 20.

## 3. Frame format

Every PDU is an octet sequence:

```
Octet 0      operation code
Octet 1      command code, sub-operation, or fragment index
Octet 2..n   operation-specific payload
```

All multi-octet numeric fields are little-endian. Signed values use two's complement.

A request is one characteristic value. A response is one notification on the API Response characteristic and begins with the same operation code as the request. The host matches a response to its request by operation code only. A host must have at most one outstanding request per connection; a bike must answer requests in the order it received them.

Unsolicited events may arrive between a request and its response. A host must not treat an event PDU as the response; where an operation defines both responses and events, the command code or sub-operation identifies which one arrived.

If the bike cannot honour a request, it sends an Error PDU:

| Octet | Field | Format |
|---|---|---|
| 0 | Operation code | `0xFE` |
| 1 | Failed operation code | `u8` |
| 2 | Error code | `u8`: 0 invalid arguments, 1 invalid state |

The bike does not send a success response for a request that produced an Error PDU.

Two control codes are reserved for permission handling:

| Code | Name | Meaning |
|---|---|---|
| `0xFA` | Permission Denied | The host is not permitted to control the bike |
| `0xFB` | Already Connected | Another controller holds control; the payload may contain that controller's address as UTF-8 text |

The SB20 does not send either code and does not enforce a handshake before accepting control requests. A host should still send the handshake described in section 5.1 before its first control request, and a bike should answer it.

## 4. Operation summary

| Code | Operation | Direction | Response |
|---|---|---|---|
| `0x00` | SCTS Identifier | Host to bike | Yes |
| `0x01` | Wireless sensor configuration | Both | Yes |
| `0x02` | Power / ERG target | Both | Yes |
| `0x03` | User and equipment weight | Both | Yes |
| `0x04` | Resistance | Both | Yes |
| `0x05` | Gradient scaling factor | Both | Yes |
| `0x06` | Zero reset (calibration) | Both | Yes |
| `0x07` | Battery percentage | Both | Yes |
| `0x08` | Power data | Both | Yes for requests; events are unsolicited |
| `0x09` | ANT scanner results | Bike to host | Unsolicited |
| `0x0A` | Power meter pairing | Both | Yes |
| `0x0B` | Buttons | Both | Yes |
| `0x0C` | Gearing (virtual drivetrain) | Both | Per sub-operation |
| `0x0D` | Sensor firmware version | Both | Yes |
| `0x0E` | Bike status | Both | Yes |
| `0x0F` | Simulation grade | Both | Yes |
| `0x10` | Parameters | Both | Yes |
| `0xFA` | Permission denied | Bike to host | None |
| `0xFB` | Already connected | Bike to host | None |
| `0xFC` | Factory control | Both | Yes |
| `0xFD` | Store settings | Both | Yes |
| `0xFE` | Error | Bike to host | None |
| `0xFF` | Long message fragment | Reserved | Not used by the SB20 |

## 5. Operations

### 5.1 Operation `0x00` — SCTS Identifier

The handshake establishes the control channel and returns the bike's identifier and protocol version.

Request:

| Octet | Field | Format |
|---|---|---|
| 0 | Operation code | `0x00` |
| 1 | Identifier | `u8` |

The usual request is `00 00`.

Response:

| Octet | Field | Format | Presence |
|---|---|---|---|
| 0 | Operation code | `0x00` | Mandatory |
| 1–2 | Identifier | `u16` | Present when the response has at least four payload octets |
| 3–4 | Version | `u16` | Present with the identifier |

The SB20 answers `00 01 00 00 00`, meaning identifier 1 and version 0. A short response may carry only an 8-bit identifier.

The bike does not require the handshake before other operations. A host should send it before its first control request for compatibility with hosts that expect a permission gate.

### 5.2 Operation `0x01` — Wireless sensor configuration

This operation configures, removes, or queries a sensor received by the bike's internal wireless receiver.

| Octet | Field | Format |
|---|---|---|
| 0 | Operation code | `0x01` |
| 1 | Command code | See table 5.2a |
| 2..n | Sensor | See table 5.2b |

Commands:

| Code | Name | Direction | Sensor field |
|---|---|---|---|
| 0 | Set left power meter (BLE) | Host to bike | BLE sensor |
| 1 | Set left power meter (ANT+) | Host to bike | ANT sensor |
| 2 | Set heart rate (BLE) | Host to bike | BLE sensor |
| 3 | Set heart rate (ANT+) | Host to bike | ANT sensor |
| 4 | Remove heart rate | Host to bike | None |
| 5 | Remove power meter | Host to bike | None |
| 6 | Set right power meter (BLE) | Host to bike | BLE sensor |
| 7 | Set right power meter (ANT+) | Host to bike | ANT sensor |
| 8 | Get left power meter | Host to bike | None |
| 9 | Left power meter (ANT+) | Bike to host | ANT sensor |
| 10 | Left power meter (BLE) | Bike to host | BLE sensor |
| 11 | Get right power meter | Host to bike | None |
| 12 | Right power meter (ANT+) | Bike to host | ANT sensor |
| 13 | Right power meter (BLE) | Bike to host | BLE sensor |

Sensor encodings:

| Sensor | Length | Layout |
|---|---|---|
| ANT sensor | 2 | `Identifier:u16`, the ANT+ device number |
| BLE sensor | 1 + n | `Identifier:UTF-8[n]` followed by `0x00` |
| None | 0 | Empty |

A get command is answered with the matching left or right sensor command and the sensor value. Sensor configuration becomes persistent only after the Store Settings operation (`0xFD`).

### 5.3 Operation `0x02` — Power and ERG target

| Octet | Field | Format |
|---|---|---|
| 0 | Operation code | `0x02` |
| 1 | Command code | See table 5.3a |
| 2–5 | Power | `u32`, unit 0.5 W |

Commands:

| Code | Name | Meaning |
|---|---|---|
| 0 | Set target power | Selects ERG mode and sets the target |
| 1 | Get calculated power | Returns the bike's calculated power |
| 2 | Get last power | Returns the most recent power value |

The power field is a 32-bit unsigned integer in 0.5 W units. A host may append four zero octets after the field; the bike ignores them. The bike reads only the first four payload octets.

A target of 0 is an ERG command for 0 W, not an exit from ERG. The bike clamps the target to 0–4000 W. A host should clamp to the range the bike supports before sending.

The response carries the same operation and command and an `i32` power value in 0.5 W units.

Setting target power selects ERG mode. In ERG mode the bike ignores gear-shift requests. To leave ERG, send a resistance or simulation PDU (section 5.5 or 5.16).

### 5.4 Operation `0x03` — User and equipment weight

| Octet | Field | Format |
|---|---|---|
| 0 | Operation code | `0x03` |
| 1 | Command code | 0 get weight, 1 set weight |
| 2–3 | User weight | `u16`, unit 0.01 kg |
| 4–5 | Equipment weight | `u16`, unit 0.01 kg |

Both weights are always present. A host that has only one value must either repeat it or send zero for the other. The response uses the same layout.

Weight becomes persistent only after the Store Settings operation (`0xFD`).

### 5.5 Operation `0x04` — Resistance

| Octet | Field | Format |
|---|---|---|
| 0 | Operation code | `0x04` |
| 1 | Command code | 0 get resistance, 1 set resistance |
| 2 | Resistance level | `u8` |

The level maps linearly to a percentage:

```
percent = level / 2.55
level   = round(percent × 2.55)
```

The range is 0–255, representing 0–100 %. Setting resistance selects level mode. Gear shifting remains active in level mode. The response carries the same command and the current level.

### 5.6 Operation `0x05` — Gradient scaling factor

| Octet | Field | Format |
|---|---|---|
| 0 | Operation code | `0x05` |
| 1 | Command code | 0 get, 1 set |
| 2–3 | Scaling factor | `u16` |
| 4 | Delay enabled | `u8`: 0 false, 1 true |

The scaling factor is a percentage. The wire value is twice the percentage, so a wire value of 100 represents a 50 % scaling factor. The factor multiplies a course gradient supplied by the host; for example, a 50 % factor makes the bike simulate half of the commanded gradient.

The response uses the same layout and carries the current factor and delay flag.

### 5.7 Operation `0x06` — Zero reset (calibration)

| Octet | Field | Format |
|---|---|---|
| 0 | Operation code | `0x06` |
| 1 | Command code | 0 request, 1 response |

A request is canonically `06 00` and may be followed by additional octets. The bike ignores the request payload and accepts either a two-octet or a longer request.

A response is variable length. The first three octets are always:

| Octet | Field | Format |
|---|---|---|
| 0 | Operation code | `0x06` |
| 1 | Command code | `0x01` |
| 2 | Sensor status | `u8` |

For status 1 (Success) and status 6 (Manufacturer Code), 13 more octets follow:

| Octet | Field | Format | Valid when |
|---|---|---|---|
| 3 | Fields valid mask | `u8` | Always present in the 16-octet form |
| 4–5 | Left offset | `i16` | Mask bit 0 set |
| 6–7 | Right offset | `i16` | Mask bit 1 set |
| 8–11 | Manufacturer code | `u32` | Mask bit 2 set |
| 12–13 | Company identifier | `u16` | Mask bit 3 set |
| 14–15 | Torque offset | `i16` | Mask bit 4 set |

Fields whose mask bit is clear are zero. The mask bits are:

| Bit | Meaning |
|---|---|
| 0 | Left offset valid |
| 1 | Right offset valid |
| 2 | Manufacturer code valid |
| 3 | Company identifier valid |
| 4 | Torque offset valid |
| 5–7 | Reserved |

Status values:

| Value | Name | Response length |
|---|---|---|
| 0 | Initiated | — |
| 1 | Success | 16 octets |
| 2 | Error: no connection | 3 octets |
| 3 | Error: timeout | 3 octets |
| 4 | Error: request | 3 octets |
| 5 | Error: invalid crank position | 3 octets |
| 6 | Manufacturer code | 16 octets |

Status is global, not per side. A three-octet error does not identify which power meter failed. On success, the mask says which offsets in that frame are valid. The bike may send more than one success frame for one request, for example a left-only frame followed by a both-sides frame. There is no ordering guarantee and no cancel or abort PDU. A second request while a reset is pending starts another attempt; the bike does not return a busy status.

The bike sends Error: no connection only when no power meter is ready. With one meter connected it may send no response at all, so a host must apply its own timeout.

The left, right, and torque offsets are opaque calibration coefficients. Their physical unit is not defined.

### 5.8 Operation `0x07` — Battery percentage

| Octet | Field | Format |
|---|---|---|
| 0 | Operation code | `0x07` |
| 1 | Command code | 0 power meter battery, 1 heart rate battery |
| 2 | Level | `i8`, percent |

The response carries the same command and the battery level. Command 1 is defined but not used.

### 5.9 Operation `0x08` — Power data

| Octet | Field | Format |
|---|---|---|
| 0 | Operation code | `0x08` |
| 1 | Command code | 0 subscribe, 1 unsubscribe, 2 event |
| 2 | Fields valid mask | `u8` |
| 3–6 | Instantaneous power | `u32`, unit 1 W |
| 7–10 | Accumulated power | `u32`, unit 1 W |
| 11–12 | Cadence | `i16`, unit 1 rpm |
| 13 | Power distribution | `u8`, percent |
| 14 | Left/right difference | `u8` |

Subscribe and unsubscribe PDUs are two octets. The bike acknowledges them with a response bearing the same operation and command. Event PDUs are 15 octets and are unsolicited.

The host must also enable notifications on the API Response characteristic. Subscribe and unsubscribe control the bike's event generation; they do not replace the CCCD write.

Validity mask:

| Bit | Meaning |
|---|---|
| 0 | Instantaneous power valid |
| 1 | Accumulated power valid |
| 2 | Cadence valid |
| 3 | Power distribution valid |
| 4 | Left/right difference valid |
| 5–7 | Reserved |

A host may interpret the power distribution field as the percentage contribution of one side, with the complementary side at `100 − value`. The protocol does not identify which side is reported. A host should clamp the value to 0–100 on reception. The sign convention of the left/right difference field is not defined by this service.

### 5.10 Operation `0x09` — ANT scanner results

The bike sends this operation only as an event.

| Octet | Field | Format | Presence |
|---|---|---|---|
| 0 | Operation code | `0x09` | Mandatory |
| 1 | Status | `u8` | Mandatory |
| 2 | Device type | `u8` | Present when status is 1 |
| 3 | RSSI | `i8` | Present when status is 1 |
| 4–5 | ANT identifier | `u16` | Present when status is 1 |

Status values:

| Value | Name |
|---|---|
| 0 | Scanning started |
| 1 | Sensor detected |
| 2 | Scanning finished |

Device type values:

| Value | Name |
|---|---|
| 0 | Power meter |
| 1 | Heart rate |

A status 0 or 2 frame is two octets. A status 1 frame is six octets.

### 5.11 Operation `0x0A` — Power meter pairing

A request has two forms:

| Octet | Field | Format | Presence |
|---|---|---|---|
| 0 | Operation code | `0x0A` | Mandatory |
| 1 | Command code | See table 5.11a | Mandatory |
| 2–3 | Crank length | `u16`, unit 0.5 mm | Present only when non-zero |

A response or notification has this form:

| Octet | Field | Format |
|---|---|---|
| 0 | Operation code | `0x0A` |
| 1 | Command code | See table 5.11a |
| 2–3 | Crank length | `u16`, unit 0.5 mm |
| 4–5 | Left ANT identifier | `u16` |
| 6–9 | Left serial number | `u32` |
| 10–11 | Right ANT identifier | `u16` |
| 12–15 | Right serial number | `u32` |

The response can be up to 16 octets. Trailing fields may be absent; a host treats missing bytes as zero.

Commands:

| Code | Name | Direction |
|---|---|---|
| 0 | Get status | Host to bike |
| 1 | Devices are paired | Bike to host |
| 2 | Device is not paired | Bike to host |
| 3 | Restart pairing | Host to bike |
| 4 | In progress | Bike to host |
| 5 | Failed | Bike to host |

The host reads pairing status with `0A 00`. The response supplies the crank length, ANT identifiers, and serial numbers. To change the crank length, send `0A 03` with the new length in 0.5 mm units; the presence of the length is authoritative and also restarts pairing. To restart pairing without changing the length, send `0A 03` alone and poll status until command 1 or 5 arrives.

### 5.12 Operation `0x0B` — Buttons

| Octet | Field | Format | Presence |
|---|---|---|---|
| 0 | Operation code | `0x0B` | Mandatory |
| 1 | Command code | 0 set, 1 get | Mandatory |
| 2–16 | Button assignments | 15 × `u8` | Present in a set request and a get response |

The set PDU is 17 octets. The 15 values are assigned in this fixed order:

```
slot 1, slot 2, slot 3, slot 6, slot 7, slot 8,
slot 11, slot 12, slot 13, slot 14, slot 15, slot 16,
slot 17, slot 18, slot 19
```

Slots 4, 5, 9, and 10 are never transmitted. A get request is `0B 01`; the response carries the same 15 values in the same order.

Assignment values:

| Value | Function | Value | Function |
|---|---|---|---|
| 0 | Undefined | 22 | SRAM plus (multishift) |
| 1 | Chainring plus (front up) | 23 | SRAM minus (multishift) |
| 2 | Chainring minus (front down) | 24 | HUD toggle |
| 3 | Cassette plus (rear up) | 25 | Map toggle |
| 4 | Cassette minus (rear down) | 26 | Skip interval |
| 5 | External | 27 | Pause |
| 6 | Not applicable | 28 | Resume |
| 7 | SRAM plus | 29 | Lap |
| 8 | SRAM minus | 30 | Previous interval |
| 9 | SRAM front toggle | 31 | Menu |
| 10 | Navigate left | 32 | Home |
| 11 | Navigate right | 33 | Push to talk |
| 12 | Navigate up | 34 | U-turn |
| 13 | Navigate down | 35 | Toggle mode |
| 14 | Select | 36 | Take a break |
| 15 | Back | 37 | Join another rider |
| 16 | Power up | 38 | Change route |
| 17 | Ride on | 39 | Camera view |
| 18 | Steer left | 40 | Screenshot |
| 19 | Steer right | 41 | Cruise control |
| 20 | Cassette plus (multishift) | 42 | Workout difficulty increase |
| 21 | Cassette minus (multishift) | 43 | Workout difficulty decrease |

The bike acts on codes 1–4 and 7–9 as shift commands. Multishift codes 20–23 are stored but do not shift the drivetrain. Other codes are stored and have no effect on the drivetrain.

Slot 19 has no physical button. A non-zero assignment in slot 19 enables the both-press front-toggle gesture.

Button assignments become persistent only after the Store Settings operation (`0xFD`).

### 5.13 Operation `0x0C` — Gearing (virtual drivetrain)

Octet 1 is a sub-operation:

| Sub-operation | Name | Direction | Purpose |
|---|---|---|---|
| 0 | Subscription | Both | Enable, disable, or query gear notifications |
| 1 | Notification | Bike to host | Current gear |
| 2 | Transfer | Both | Transfer, store, or apply a drivetrain model |
| 3 | Fragment | Both | Drivetrain map payload fragment |
| 4 | Manual target power | Bike to host | Advisory report of a manually selected ERG target |

Sub-operation 4 is the two-octet PDU `0C 04`. It carries no payload.

#### 5.13.1 Subscription

| Octet | Field | Format |
|---|---|---|
| 0–1 | Operation, sub-operation | `0x0C`, `0x00` |
| 2 | Command | 0 unsubscribe, 1 subscribe, 2 get |

The bike acknowledges the request. Subscribing enables the notification sub-operation for the connection; the host must also enable notifications on the API Response characteristic.

#### 5.13.2 Notification

| Octet | Field | Format |
|---|---|---|
| 0–1 | Operation, sub-operation | `0x0C`, `0x01` |
| 2 | Model type | `u8` |
| 3 | Front index | `u8`, 1-based |
| 4 | Rear index | `u8`, 1-based |
| 5–6 | Front teeth | `u16` |
| 7–8 | Rear teeth | `u16` |
| 9 | Dream Drive flag | `u8`: 0 false, 1 true |
| 10 | Dream Drive step | `u8` |

The notification is 11 octets and reports the currently selected gear.

#### 5.13.3 Transfer

The full transfer PDU is 11 octets:

| Octet | Field | Format | Presence |
|---|---|---|---|
| 0–1 | Operation, sub-operation | `0x0C`, `0x02` | Mandatory |
| 2 | Command | See table 5.13a | Mandatory |
| 3 | Model type | `u8` | Present in request, store, apply, and header responses |
| 4 | Front sprocket count | `u8` | Same |
| 5 | Rear sprocket count | `u8` | Same |
| 6 | Map CRC-8 | `u8` | Same |
| 7 | Dream Drive flag | `u8`: 0 false, 1 true | Same |
| 8 | Dream Drive step | `u8` | Same |
| 9–10 | Map size | `u16`, octets | Same |

A result-only response may be three octets (`0C 02 command`). A receiver treats absent trailing fields as zero.

Commands:

| Code | Name | Direction |
|---|---|---|
| 0 | Request transfer | Host to bike |
| 1 | Request transfer success | Bike to host |
| 2 | Request transfer error | Bike to host |
| 3 | Store | Host to bike |
| 4 | Store success | Bike to host |
| 5 | Store invalid CRC error | Bike to host |
| 6 | Store internal error | Bike to host |
| 7 | Get current model | Host to bike |
| 8 | Get current model header | Bike to host |
| 9 | Get current model success | Bike to host |
| 10 | Store in progress | Bike to host |
| 11 | Apply | Host to bike |
| 12 | Apply success | Bike to host |

The host sends Request Transfer, then the map fragments, then Apply. Store is a separate persistence step.

#### 5.13.4 Model types and map contents

| Value | Name | Map content |
|---|---|---|
| 0 | Front/rear, 8-bit | One `u8` per sprocket: the front ring teeth list, then the cassette teeth list |
| 1 | Front/rear, 16-bit | One `u16` per sprocket: the front ring list, then the cassette list |
| 2 | Coefficients | One `u16` per rear sprocket |

For model type 0:

```
map size  = front count + rear count
gear count = front count × rear count
```

For model type 1:

```
map size  = 2 × (front count + rear count)
gear count = front count × rear count
```

For model type 2:

```
map size  = 2 × front count × rear count
gear count = front count × rear count
```

The map contains only rear sprockets, so a transfer of model type 2 must set front count to 1. The gear count is then the rear count. The front ring is not represented.

A model type 2 coefficient is `u16` little-endian and is calculated as:

```
coefficient = truncate((1 / v) × 1000)
```

where `v` is the inverse virtual ratio stored for that gear. The bike treats the coefficient as a virtual ratio, not as a tooth count.

#### 5.13.5 CRC-8

The map checksum covers the map payload only; the 8-octet transfer header is excluded. The algorithm is CRC-8 with polynomial `0x31`, initial value `0xFF`, most-significant bit first, no reflection, and no final XOR:

```
crc = 0xFF
for each map octet b:
    crc = crc XOR b
    repeat 8 times:
        if crc AND 0x80:
            crc = ((crc << 1) XOR 0x31) AND 0xFF
        else:
            crc = (crc << 1) AND 0xFF
```

Test vectors:

```
crc8(22 24 20 1C 18 15 13 11) = 0xE1
crc8(22 24 20 1C 18 15 13 11 0F 0D 0B 09 07) = 0x79
```

#### 5.13.6 Fragments

Map data is sent as fragments:

| Octet | Field | Format |
|---|---|---|
| 0–1 | Operation, sub-operation | `0x0C`, `0x03` |
| 2..n | Map data | Up to 18 octets |

Each fragment is one characteristic value. Fragments are fire-and-forget: the bike sends no acknowledgement for an individual fragment. The bike appends fragments in arrival order and does not use a fragment index. The map size declared in the transfer header determines when the map is complete. Duplicate or out-of-order fragments corrupt the map; the CRC check at Apply or Store detects the result.

#### 5.13.7 Dream Drive

Dream Drive applies only to model type 2 when the Dream Drive flag is set. The step is stored in the transfer header; it is not a per-press value.

When the host shifts in the front/chainring direction, the rear index changes by the step. When it shifts in the rear direction, the rear index changes by one. The result is clamped to the valid rear range and does not wrap. A step of 0 makes a front-direction shift a no-op. A step larger than the remaining range clamps to the first or last gear.

Because model type 2 requires front count 1, only the rear index moves.

#### 5.13.8 Gearing procedure

1. Subscribe to gear notifications with `0C 00 01`.
2. Send Request Transfer (`0C 02 00`) with the model type, counts, CRC, Dream Drive flag and step, and map size.
3. Wait for Request Transfer Success (`0C 02 01`).
4. Send the map as `0C 03` fragments of at most 18 octets each.
5. Send Apply (`0C 02 0B`) with the same header. Wait for Apply Success (`0C 02 0C`).
6. Optionally send the Store command (`0C 02 03`) to persist the map. The bike answers In Progress (`0C 02 0A`) and then Success (`0C 02 04`) or Internal Error (`0C 02 06`). A length or CRC failure answers Invalid CRC Error (`0C 02 05`). If the stored map already matches, the bike answers Success (`0C 02 04`) without writing. A missing buffer or internal state answers Internal Error (`0C 02 06`).

Apply installs the map in RAM and clears the transfer request. It does not write flash. Store validates the same length and CRC and writes the map to flash. A host that needs the map to survive a power cycle must send Store after Apply.

Request Transfer failures answer `0C 02 02`. Apply failures answer `0C 02 05`.

Store applies a new map live before queuing the flash write. If neither Success nor Internal Error arrives, the flash task has not completed and the bike remains busy until a power cycle. A second Apply with no pending transfer request returns Apply Success without applying. The bike has no receive-side timeout; a host must apply its own.

#### 5.13.9 Reading the current model

`0C 02 07` requests the current model. The bike sends a header with command 8, map fragments with sub-operation 3, and a final header with command 9. The SB20 returns the header reliably, but the map stream may be incomplete; only the header is reliable. A host should use the header for model type, counts, CRC, Dream Drive flag, and map size, and should not assume that the map body is complete.

### 5.14 Operation `0x0D` — Sensor firmware version

Two response encodings exist.

Verbose form:

| Octet | Field | Format | Presence |
|---|---|---|---|
| 0 | Operation code | `0x0D` | Mandatory |
| 1 | Command code | See table 5.14a | Mandatory |
| 2–3 | ANT identifier | `u16` | Present when a version is returned |
| 4..n | Version | UTF-8 text | Present when a version is returned |

Binary form (9 octets):

| Octet | Field | Format |
|---|---|---|
| 0 | Operation code | `0x0D` |
| 1 | Command code | See table 5.14a |
| 2 | Major | `u8` |
| 3 | Minor | `u8` |
| 4 | Patch | `u8` |
| 5–6 | Manufacturer identifier | `u16` |
| 7–8 | ANT identifier | `u16` |

Commands:

| Code | Name | Direction |
|---|---|---|
| 0 | Subscribe | Host to bike |
| 1 | Unsubscribe | Host to bike |
| 2 | Get left firmware version | Host to bike |
| 3 | No firmware version | Bike to host |
| 4 | Get right firmware version | Host to bike |
| 10 | Firmware version | Bike to host |

A version response uses command 10 in either encoding. Command 3 reports that no version is available.

### 5.15 Operation `0x0E` — Bike status

| Octet | Field | Format |
|---|---|---|
| 0 | Operation code | `0x0E` |
| 1 | Command code | 0 get bike status |
| 2–3 | Flywheel speed | `u16`, unit 0.25 rpm |
| 4 | Heart rate monitor status | `u8` |
| 5 | Power meter status | `u8` |
| 6 | Power meter pairing status | `u8` |

The PDU is 7 octets. Flywheel speed is the only rotational quantity the protocol reports; it is in quarter-rpm units, so a host divides by four to obtain rpm.

Sensor status values:

| Value | Name |
|---|---|
| 0 | Disconnected |
| 1 | Searching |
| 2 | Connected |
| 3 | Connected, low battery |

Power meter pairing status values:

| Value | Name |
|---|---|
| 0 | Unknown |
| 1 | Paired |
| 2 | Not paired |

### 5.16 Operation `0x0F` — Simulation grade

| Octet | Field | Format | Presence |
|---|---|---|---|
| 0 | Operation code | `0x0F` | Mandatory |
| 1 | Command code | 0 set grade, 1 release control | Mandatory |
| 2–5 | Grade | `i32`, unit 0.001 % | Present for command 0 |

The grade field is a signed 32-bit integer in thousandths of a percent. The host may append four zero octets after the field; the bike ignores them. A host should clamp the grade to −20.00 % to +20.00 % before encoding.

Setting a grade selects simulation mode. Gear shifting remains active in simulation mode. Command 1 is reserved for releasing control; its behaviour is not defined.

### 5.17 Operation `0x10` — Parameters

| Octet | Field | Format |
|---|---|---|
| 0 | Operation code | `0x10` |
| 1 | Command code | 0 set parameters, 1 get parameters |
| 2 | Brake enabled | `u8`: 0 false, 1 true, 2 unchanged |
| 3 | Dual BLE enabled | `u8`: 0 unchanged, 1 false, 2 true |

The PDU is 4 octets. The two fields use different tri-state encodings: brake uses 2 for unchanged, while dual BLE uses 0 for unchanged.

A get response carries the current values. A bike that has no value for a field should return the field's default with the false encoding.

The brake flag controls the bike's internal flywheel brake only. It does not affect the reporting of brake-lever positions. Changes become persistent only after the Store Settings operation (`0xFD`).

### 5.18 Operation `0xFC` — Factory control

| Octet | Field | Format |
|---|---|---|
| 0 | Operation code | `0xFC` |
| 1 | Command code | See table 5.18a |
| 2..n | Parameters | Command-specific |

Commands:

| Code | Name |
|---|---|
| 0 | Factory reset |
| 1 | Factory reset in progress |
| 2 | Get status |
| 3 | Status not configured |
| 4 | Status configured |
| 5 | Set UICR |
| 6 | Set UICR in progress |
| 7 | Error: invalid CRC |
| 8 | UICR not changed |
| 9 | Get UICR |
| 10 | No UICR stored |

Command 0 carries the literal ASCII string `ErAsE_BikE` after the command octet (10 octets).

Command 5 carries:

| Octet | Field | Format |
|---|---|---|
| 0 | Operation code | `0xFC` |
| 1 | Command code | `0x05` |
| 2–3 | Left ANT identifier | `u16` |
| 4–5 | Right ANT identifier | `u16` |
| 6–16 | Serial number | 11 ASCII octets |
| 17 | CRC-8 | `u8` |

The CRC-8 uses the same polynomial, initial value, and bit order as the drivetrain map CRC, and covers the preceding 15-octet payload: the two ANT identifiers and the 11-octet serial.

The response echoes the command code and is two octets. The other commands carry no parameters.

### 5.19 Operation `0xFD` — Store settings

| Octet | Field | Format |
|---|---|---|
| 0 | Operation code | `0xFD` |
| 1 | Command code | See table 5.19a |

Commands:

| Code | Name |
|---|---|
| 0 | Store |
| 1 | In progress |
| 2 | Error |
| 3 | Stored |
| 4 | Stored and applied |

The bike answers a Store request with In Progress (`FD 01`), then Stored (`FD 03`) followed by Stored and Applied (`FD 04`) when a flash write occurred. If the record already matches, the bike sends only Stored and Applied. A busy write answers Error (`FD 02`).

The store persists the settings record: user and equipment weight, the 15 button assignments, gradient scaling, crank length, sensor and ANT configuration, brake enable, and dual BLE. It does not persist the gearing map; the gearing Store command (`0x0C`/2/3) does that.

A configuration Set updates RAM and emits an unsolicited Stored and Applied (`FD 04`). A Stored and Applied response after a Set does not prove that the value was written to flash. A host that needs a change to survive a power cycle must send `FD 00` and wait for `FD 03`.

The store is idempotent. Repeating it with an unchanged record performs no flash write.

### 5.20 Special operation codes

| Code | Name | Definition |
|---|---|---|
| `0xFA` | Permission denied | Reserved; not sent by the SB20 |
| `0xFB` | Already connected | Reserved; not sent by the SB20 |
| `0xFE` | Error | Section 3 |
| `0xFF` | Long message fragment | Reserved; not used by the SB20 |

The generic `0xFF` fragmentation form is not accepted by the SB20. Drivetrain maps use the operation-owned fragments of section 5.13.6 instead.

## 6. Procedures

### 6.1 Connection and subscription

1. Scan for the Fitness Machine service `0x1826` and connect.
2. Discover the API service `0c46beaf-9c22-48ff-ae0e-c6eae1a2f4e5` and both characteristics.
3. Enable notifications on the API Response characteristic by writing `0x0001` to its CCCD.
4. Send the SCTS Identifier handshake (`00 00`) and wait for the response.
5. Leave the ATT MTU at 23. A larger MTU does not increase the 20-octet PDU limit.

If either API characteristic is missing, the connection cannot carry this protocol.

### 6.2 Control modes

The most recent successful control PDU selects the bike's mode:

| Mode | Request | Effect |
|---|---|---|
| ERG | `0x02` command 0 with target power in 0.5 W units | The bike holds the target power and ignores gear shifts |
| Simulation | `0x0F` command 0 with grade in 0.001 % units | The bike simulates the grade and applies gear shifts |
| Level | `0x04` command 1 with a level byte | The bike applies the resistance level and applies gear shifts |
| External | No PDU | The bike keeps its current mode; it cannot leave ERG |

A host enters ERG by sending a target power. There is no separate ERG-on command. A target of 0 is an ERG command for 0 W, not an exit.

A host leaves ERG by sending a level or simulation PDU. Level 0 and grade 0.000 % are neutral backgrounds that still restore gear shifting.

A gearing Apply (`0x0C`/2/11) installs a drivetrain map but does not change the control mode. Send the map first, then the level or simulation PDU.

The bike advertises the Fitness Machine service `0x1826` and its Control Point `0x2AD9`, but this protocol does not use the FTMS Control Point for ERG, simulation, or level. Use the proprietary operations above for control.

### 6.3 Sensor configuration, pairing, and calibration

1. Set each sensor with the appropriate `0x01` command and sensor encoding.
2. Send Store Settings (`0xFD`/0) after a batch of sensor changes, and wait for Stored (`FD 03`).
3. Start power meter pairing with `0x0A`/3. To change the crank length at the same time, include it in the pairing request in 0.5 mm units. Poll status until Devices Are Paired (1) or Failed (5) arrives.
4. Request zero reset with `0x06`/0. The calibration completes when the bike returns command 1 with a sensor status.

### 6.4 Persistence

| Data | Persisted by `0xFD` | Persisted by its own operation | Survives reconnect | Survives power cycle |
|---|---|---|---|---|
| Weight, gradient scaling, buttons, brake and dual BLE, sensor and ANT configuration, crank length | Yes | No | Yes, while powered | Only after `0xFD`/0 and `FD 03` |
| Drivetrain map, model type, counts, CRC, Dream Drive flag and step | No | Apply installs in RAM; Store (`0x0C`/2/3) writes flash | Active map survives reconnect | Stored map survives a power cycle; RAM-only map does not |

A configuration Set updates RAM and emits `FD 04` without writing flash. Do not treat `FD 04` as proof of persistence. Send `FD 00` and require `FD 03`.

A gearing Apply is RAM-only. Send `0x0C`/2/3 and wait for Store Success (`04`) if the map must survive a power cycle.

### 6.5 Transaction rules

| Rule | Requirement |
|---|---|
| Outstanding requests | At most one per connection |
| Response matching | By operation code only |
| Timeout | 30 seconds per transaction is a practical default |
| Retry | Do not retry automatically; a retry is a new request |
| Interleaved events | Events may arrive during a transaction and must not be matched as the response |
| Error handling | An Error PDU terminates the transaction; do not expect a success response as well |

## 7. Other GATT services on the bike

The bike advertises the Bluetooth SIG Fitness Machine service (`0x1826`) and the Cycling Speed and Cadence service (`0x1816`). This protocol does not use either for control. The Fitness Machine Control Point (`0x2AD9`) is present and can be subscribed to, but the proprietary operations in this document are the control path.

The Device Information service (`0x180A`) provides model, serial, firmware, hardware, software, and manufacturer strings. A host may read it for identification.

Firmware updates use the Nordic Secure DFU service `0000fe59-0000-1000-8000-00805f9b34fb`. The DFU protocol is outside this document.

## 8. Security

The service uses open access. A host does not need encryption, authentication, or bonding to send requests or receive notifications. The SB20 does not enforce the SCTS handshake as a permission gate, and it does not send the permission or already-connected control codes.

The service contains requests that change the bike's mechanical behaviour: target power, resistance, grade, calibration, and drivetrain configuration. A host should validate values before sending them. A bike that supports bonding should restrict control to a bonded peer, and it must apply the same parameter limits it applies to its own user interface.
