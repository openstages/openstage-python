# Elite Square profile over WFTNP

Status: implementation specification for a WFTNP server that presents an Elite Square-compatible controller and drivetrain source. The outer transport is defined in `wftnp-transport-spec.md`.

## 1. Scope

This profile carries two independent streams:

- A status stream with active profile, battery level, pressed-button bitmask, and drivetrain geometry or sequential gear state.
- A button stream with twenty rolling button counters.

The client subscribes to both characteristics, reads their current values, and publishes gear changes and button actions to the application.

## 2. Service and characteristic UUIDs

| Role | UUID | Properties |
|---|---|---|
| Service | `347B0001-7635-408B-8918-8FF3949CE592` | Primary |
| Status | `347B0043-7635-408B-8918-8FF3949CE592` | Read, Notify |
| Buttons | `347B0045-7635-408B-8918-8FF3949CE592` | Read, Notify |

The WFTNP characteristic property byte for both characteristics is `0x05` (read and notify).

The status characteristic carries the drivetrain state. The button characteristic carries edge counters. Neither characteristic is writable in this profile.

## 3. Advertising requirements

The client recognizes an Elite Square device from two facts:

1. The advertised name contains the substring `square`, compared case-insensitively.
2. The advertised service list contains `347B0001-7635-408B-8918-8FF3949CE592`.

Both conditions must hold. A device that exposes the service but uses another name is treated as an Elite trainer rather than an Elite Square controller.

WFTNP TXT example:

```text
serial-number=SQ-0001
mac-address=02:00:00:00:00:01
ble-service-uuids=347B0001-7635-408B-8918-8FF3949CE592
```

The mDNS instance name must contain `square`, for example `Square Bridge`.

## 4. Connection and discovery

1. The server returns `347B0001-7635-408B-8918-8FF3949CE592` in Discover Services.
2. The client discovers characteristics `347B0043` and `347B0045`.
3. The client enables notifications on both characteristics.
4. The client reads both characteristics to obtain the initial state.
5. The server notifies when the status or button counters change.

The server should answer reads immediately. The client uses the initial read to populate gear state before the first notification arrives.

## 5. Status characteristic

### 5.1 Layout

The value is 12 to 14 bytes. A 12-byte value carries the common fields and the sequential fields. A 14-byte value carries the non-sequential gear geometry.

| Offset | Size | Field | Encoding |
|---:|---:|---|---|
| 0 | 1 | Flags | Bit 0: sequential gear mode. Other bits reserved. |
| 1 | 1 | Active profile | Unsigned byte |
| 2 | 1 | Battery percent | Unsigned byte, 0–100 |
| 3 | 4 | Pressed buttons | Unsigned 32-bit big-endian bitmask |
| 7 | 1 | Reserved | Send `0x00` |

Offsets 8–13 depend on the mode. All of these bytes are unsigned.

| Offset | Size | Sequential mode | Non-sequential mode |
|---:|---:|---|---|
| 8 | 1 | Total gear count | Front gear count |
| 9 | 1 | Ratio high byte | Rear gear count |
| 10 | 1 | Ratio low byte | Front teeth count |
| 11 | 1 | Current gear, 1-based | Rear teeth count |
| 12 | 1 | — | Current front gear, 1-based |
| 13 | 1 | — | Current rear gear, 1-based |

The 4-byte button mask at offset 3 is big-endian. Bit 0 corresponds to button 1, bit 1 to button 2, and so on. The profile defines twenty buttons, so only the low twenty bits are meaningful.

### 5.2 Sequential mode

When bit 0 of the flags byte is set:

| Offset | Field |
|---:|---|
| 8 | Total number of sequential gears |
| 9–10 | Sequential ratio, unsigned 16-bit big-endian: high byte first |
| 11 | Current sequential gear, 1-based |

The client publishes a virtual gear using `current sequential gear - 1` as the rear index. The ratio field is stored as a raw unsigned 16-bit value and is not used for shifting or gear display. The value is informational for this client.

Set the ratio to 0 if you have no ratio, or to the same fixed-point value your drivetrain reports. The client does not define a scale and does not interpret the number.

Example: sequential, profile 1, battery 87%, no buttons pressed, 14 gears, raw ratio `0x09C4` (2500, scale device-defined), current gear 7.

```text
01 01 57 00 00 00 00 00 0E 09 C4 07
```

### 5.3 Non-sequential mode

When bit 0 of the flags byte is clear, and the value is at least 14 bytes:

| Offset | Field |
|---:|---|
| 8 | Front gear count |
| 9 | Rear gear count |
| 10 | Front teeth count |
| 11 | Rear teeth count |
| 12 | Current front gear, 1-based |
| 13 | Current rear gear, 1-based |

The client validates that the current front gear does not exceed the front gear count and that the current rear gear does not exceed the rear gear count. A value that exceeds its count is logged and ignored. Zero is accepted and maps to index zero, so the first usable gear is 1.

Example: non-sequential, profile 1, battery 87%, no buttons pressed, 2 front gears, 12 rear gears, 52 front teeth, 28 rear teeth, current front 1, current rear 5.

```text
00 01 57 00 00 00 00 00 02 0C 34 1C 01 05
```

### 5.4 Pressed-button mask

The mask at offset 3 is an instantaneous snapshot. It is independent of the rolling counters on the button characteristic.

| Bit | Mask | Button |
|---:|---:|---|
| 0 | `0x00000001` | 1 |
| 1 | `0x00000002` | 2 |
| ... | ... | ... |
| 19 | `0x00080000` | 20 |

The client uses the button characteristic for edge-triggered actions. The mask in the status characteristic is useful for diagnostics and for state that must survive a missed notification.

## 6. Button characteristic

### 6.1 Layout

The value is 11 bytes:

| Offset | Size | Field |
|---:|---:|---|
| 0 | 1 | Page index. Must be `0x00`. |
| 1–10 | 10 | Twenty 4-bit counters, two buttons per byte |

The low nibble of each byte is the lower-numbered button, and the high nibble is the next button:

| Byte | Low nibble | High nibble |
|---:|---|---|
| 1 | Button 1 | Button 2 |
| 2 | Button 3 | Button 4 |
| ... | ... | ... |
| 10 | Button 19 | Button 20 |

A counter value with bit 0 set means pressed; bit 0 clear means released. The upper three bits of each nibble are a rolling counter that the device increments on each edge, and the client only examines bit 0.

Practical encoding:

On a press transition, increment the counter and ensure the low bit is 1; a release transition also increments the counter but leaves the low bit at 0. Send a notification only when at least one counter changes.

A page index other than zero is invalid. The client rejects the value and logs an error.

### 6.2 Examples

Button 1 pressed, button 2 released, all other counters zero:

```text
00 21 00 00 00 00 00 00 00 00 00
```

Button 1 released, button 2 released:

```text
00 22 00 00 00 00 00 00 00 00 00
```

Button 3 pressed (byte 2 low nibble), all other counters zero:

```text
00 00 01 00 00 00 00 00 00 00 00
```

Buttons 1 and 2 both pressed:

```text
00 11 00 00 00 00 00 00 00 00 00
```

## 7. Button-to-action mapping

The client maps button numbers to application actions. A server that wants a specific action emits the corresponding button number.

| Button | Action |
|---:|---|
| 1 | Navigate left |
| 2 | Navigate up |
| 3 | Navigate right |
| 4 | Navigate down |
| 9 | Increase |
| 10 | Decrease |
| 11 | Button A |
| 12 | Button Y |
| 13 | Button Z |
| 14 | Button B |
| 19 | Increase |
| 20 | Decrease |

Other button numbers are ignored by the default mapping. A controller with fewer physical buttons can still use any of these numbers.

### 7.1 Physical layout and client button numbers

The physical button labels for the Elite Square are listed below. The client does not carry names; it only numbers the counters. The table maps the labels to the client's button numbers by applying the layout in Section 6.1 to the raw byte and nibble positions. Treat the client numbers and actions as authoritative; the labels are an aid for choosing which physical button to route.

| Position | Raw byte.nibble | Physical label | Client button | Client action |
|---:|---|---|---:|---|
| 1–2 | 0 high, 0 low | N/A | — | Page byte, ignored |
| 3 | 1 high | Up | 2 | Navigate up |
| 4 | 1 low | Left | 1 | Navigate left |
| 5 | 2 high | Down | 4 | Navigate down |
| 6 | 2 low | Right | 3 | Navigate right |
| 7 | 3 high | X | 6 | None |
| 8 | 3 low | Square | 5 | None |
| 9 | 4 high | Left Campagnolo | 8 | None |
| 10 | 4 low | Left brake | 7 | None |
| 11 | 5 high | Left shift 1 | 10 | Decrease |
| 12 | 5 low | Left shift 2 | 9 | Increase |
| 13 | 6 high | Y | 12 | Button Y |
| 14 | 6 low | A | 11 | Button A |
| 15 | 7 high | B | 14 | Button B |
| 16 | 7 low | Z | 13 | Button Z |
| 17 | 8 high | Circle | 16 | None |
| 18 | 8 low | Triangle | 15 | None |
| 20 | 9 high | Right Campagnolo | 18 | None |
| 21 | 9 low | Right brake | 17 | None |
| 22 | 10 high | Right shift 1 | 20 | Decrease |
| 23 | 10 low | Right shift 2 | 19 | Increase |

The mapping has three consequences for a server:

- The first byte is 0 in the sample values. The client requires it to be 0 and treats it as a page index, so positions 1 and 2 are not usable buttons.
- The client enumerates the low nibble before the high nibble, while the physical layout lists the high nibble first. The table already accounts for this inversion.
- The client's only shift actions are single-step Increase and Decrease. A three-gear shift has no representation in this mapping. A server that needs a three-step shift must emit three separate press/release pairs or use a different profile.

The raw 11-byte samples match the layout in Section 6.1 (11 bytes, first byte 0, ten counter bytes). No additional page, length, or checksum field is present.

### 7.2 Two-stage action mapping and user remapping

The client maps a physical button to an in-game action in two stages:

1. The Elite adapter maps a button number to a controller command. This stage is fixed by the profile and is the table in Section 7.
2. The client maps that command to a UI action. This stage is user-editable and is stored per device ID and remote-control type.

The action column in the tables above is the default for the second stage. The remap dialog lets the user bind a produced command to a different action. Available actions include:

- Increase and Decrease
- Navigate up, down, left, and right
- Select and Back
- Screenshot
- Pause/resume
- Hide interface
- Skip workout segment
- Toggle ERG mode
- Kudos
- Create lap
- Chat toggle

Only commands that the Elite adapter actually produces can be bound. Buttons whose numbers are not in the mapping table produce no command and therefore cannot be remapped. Keep button numbers stable across sessions; bindings are keyed by device ID and remote-control type, so a changed button number breaks the user's saved mapping.

## 8. Gearing behavior

The client publishes a virtual gear when the current gear changes. The published gear uses zero-based indexes derived from the one-based values in the status characteristic:

- Non-sequential: front index = current front gear − 1, rear index = current rear gear − 1.
- Sequential: rear index = current sequential gear − 1, front index is unspecified.

The client also records front and rear tooth counts. These are informational for the default virtual gear publication; send accurate values if the application displays them.

Send a status notification whenever any of these changes:

- Sequential mode flag
- Active profile
- Battery percent
- Pressed-button mask
- Gear counts, teeth counts, current gear, or sequential gear

A periodic refresh at 1 Hz is acceptable and helps recover from a missed notification. Do not send a notification more than 10 times per second.

## 9. Read and notification behavior

A read of `347B0043` returns the current status value, and a read of `347B0045` returns the current button page. Both characteristics notify only after the client enables notifications, using the WFTNP Characteristic Notification frame with the characteristic UUID and the value. The server may send a notification immediately after enable, before the client's initial read completes, and the client handles either order.

## 10. Coexistence with other profiles

The client creates at most one of the Zwift and Elite services per device. If the discovered service set includes either Zwift service UUID (`00000001-19CA-4651-86E5-FA29DCDD09D1` or `0000FC82-0000-1000-8000-00805F9B34FB`), Elite service construction is skipped.

Advertise the Elite profile alone, or use a separate WFTNP device and advertisement for the Zwift profile. Do not expect both services to be active on one connection.

## 10.1 Gear display prerequisites

The gear stream is necessary but not sufficient. The client displays the shifting widget only when the device assigned as the bike set's resistance controller meets these conditions:

1. The resistance controller reports an active shifting indication. A workout in ERG mode does not display gears, because the indication requires simulation resistance mode.
2. The bike set builds a shifting adapter only for its resistance controller, the device the user assigned as the controllable source.
3. Virtual gearing support is present when the controller offers a target-gear command or an internal shifting indicator.
4. The adapter is created when that support is present. With virtual shifting enabled, a target-gear command is required; with virtual shifting disabled, an internal shifting indicator is required.
5. The client retries the adapter rebuild while it is missing, so a capability learned after source assignment is picked up.

The Elite Square service sets both capabilities that matter:

- Discovering `347B0043` marks the device as having internal shifting.
- Discovering `347B0045` marks it as a remote control, so the client adopts the Elite Square remote.

It does not advertise simulation-parameter support or a cycling-controllable source type. An Elite Square-only device is therefore a remote control and a gear source, not a controllable trainer. If another device such as a Stages SB20 is assigned as the resistance controller, the adapter reads that device's capabilities and never sees the Elite Square's internal shifting flag. That is the usual reason the client subscribes to `347B0043`/`347B0045` and still hides the gear display.

To make the check pass over WFTNP:

| Requirement | Detail |
|---|---|
| Controllable service | expose a controllable BLE service on the same WFTNP device, so the WFTNP device can be selected as the bike set's controllable/resistance source |
| FTMS choice | service `00001826-0000-1000-8000-00805F9B34FB`, feature `00002ACC-0000-1000-8000-00805F9B34FB` (read), indoor bike data `00002AD2-0000-1000-8000-00805F9B34FB` (notify), and control point `00002AD9-0000-1000-8000-00805F9B34FB` (write/indicate) |
| Feature value | 8 bytes, two little-endian 32-bit fields for supported features and target-setting features; set target-setting bit 13 (`0x2000`, simulation) and bit 3 (`0x0008`, power target) so the client accepts simulation-parameter and target-power commands and marks the device as a controllable source |
| Readiness | the client requires the feature read and an indoor bike data subscription before it treats the FTMS service as ready; until then the feature's commands stay pending and the controllable source type may not reach the device, so relay the SB20's power and cadence into `00002AD2` or send a valid minimal indoor bike data frame |
| Assignment | assign the WFTNP device as the controllable in the bike device set; the SB20 can remain the power and cadence source |
| Elite stream | the Elite `347B0043`/`347B0045` stream then supplies the gears and buttons, and no virtual-shifting toggle is needed |
| Ride mode | use simulation (route or slope) mode; in ERG mode the resistance mode is erg, so the widget stays hidden by design |

The FTMS control point is optional for the capability check but recommended for the ride: the client switches to simulation resistance mode before writing a target slope, so the gear widget can appear even if the write fails, but resistance commands keep failing without a control point that answers `RequestControl`, `Start`/`Resume`, and `SetTargetSimulationParameters`.

## 11. Implementation checklist

| Requirement | Detail |
|---|---|
| Service | expose `347B0001-7635-408B-8918-8FF3949CE592` |
| Status characteristic | `347B0043`, read and notify |
| Button characteristic | `347B0045`, same properties |
| Advertised name | include `square` |
| TXT record | include the service UUID in `ble-service-uuids` |
| Status value | 12 or 14 bytes, with the button mask big-endian |
| Button value | 11-byte page 0 |
| Gear values | one-based current gear in non-sequential mode |
| Button counters | increment the 4-bit counter on each edge and keep bit 0 as the pressed state |
| Change reporting | notify on change; a 1 Hz refresh is optional |
| Disconnect | reset counters to a released state when the TCP connection closes |
| Controllable service | expose FTMS `0x1826` or another service that advertises simulation-parameter support on the same WFTNP device; the Elite profile alone cannot be selected as the resistance controller |
| Device assignment | assign the WFTNP device as the bike set's controllable/resistance source and keep other bikes as power and cadence sources |
| Verification | confirm the gear widget in a simulation ride, not an ERG workout |
