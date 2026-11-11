# Stages SB20 button and brake input service

This document defines the vendor-specific GATT service that reports the physical button and brake-lever state of a Stages SB20 indoor bike. The reader is assumed to be familiar with Bluetooth GATT services, characteristics, notifications, and the Client Characteristic Configuration descriptor (CCCD).

The service is notification-only. It carries no commands and does not change the bike's behaviour.

## 1. Service declaration

| Item | Value |
|---|---|
| Service UUID | `0c46be5f-9c22-48ff-ae0e-c6eae1a2f4e5` |
| Service type | Primary |
| Advertising | The service is not advertised. A host discovers it after connecting. |
| Access | Notify only; no readable or writable characteristic values |

The bike also advertises the Bluetooth SIG Fitness Machine service (`0x1826`) and Cycling Speed and Cadence service (`0x1816`). Neither is used by this input service.

### 1.1 Characteristics

| Characteristic | UUID | Properties |
|---|---|---|
| Button Active | `0c46be60-9c22-48ff-ae0e-c6eae1a2f4e5` | Notify |
| Brake Value | `0c46be61-9c22-48ff-ae0e-c6eae1a2f4e5` | Notify |

Both characteristics have a CCCD (`0x2902`). A host enables notifications by writing `0x0001` to the CCCD. The bike accepts that write without bonding.

## 2. Data representation

All multi-octet values are unsigned little-endian integers. Notification values are at most 20 octets, so no fragmentation is defined.

## 3. Button Active characteristic

The Button Active value reports button activity as a two-octet header followed by one or more 16-bit event fields.

| Octet | Field | Format | Presence |
|---|---|---|---|
| 0 | Event-presence selector | `uint8` | Mandatory |
| 1 | Header byte | `uint8` | Mandatory; reserved, send `0x00` and ignore on receive |
| 2–3 | First event field | `uint16` | Mandatory |
| 4–5 | Second event field | `uint16` | Present when a second selector bit is set |
| 6–7 | Third event field | `uint16` | Present when a third selector bit is set |
| 8–9 | Fourth event field | `uint16` | Present when a fourth selector bit is set |

The low four bits of the selector name the event types that are present:

| Selector bit | Event type |
|---|---|
| 0 | Press |
| 1 | Auto-repeat |
| 2 | Long release |
| 3 | Short release |

For every set selector bit, one 16-bit field follows the header. Fields appear in ascending selector-bit order. A frame therefore contains at least one field and is at least four octets long; a frame with all four event types is ten octets long.

The selector describes the frame contents. It is not a sequence number, and it is not part of the button mask.

### 3.1 Button mask

Each event field is a bitmask over button slots. The mapping is the same for every event type.

| Bit | Slot | Bit | Slot |
|---|---|---|---|
| 0 | 1 | 7 | 12 |
| 1 | 2 | 8 | 13 |
| 2 | 3 | 9 | 14 |
| 3 | 6 | 10 | 15 |
| 4 | 7 | 11 | 16 |
| 5 | 8 | 12 | 17 |
| 6 | 11 | 13 | 18 |
| 14–15 | Reserved, send zero | — | — |

Slot 19 is not reported by this characteristic.

A set bit in a press field means that the slot is pressed. A set bit in a release field identifies the slot whose release the event reports. A receiver that does not decode the selector can still use the first field as an activity mask, but it cannot distinguish a press from a release.

### 3.2 Examples

| Frame | Meaning |
|---|---|
| `01 00 08 00` | Selector bit 0: press. Mask `0x0008` sets bit 3, so slot 6 is pressed. |
| `08 00 10 00` | Selector bit 3: short release. Mask `0x0010` sets bit 4, so slot 7 is released. |
| `01 00 02 00` | Selector bit 0: press. Mask `0x0002` sets bit 1, so slot 2 is pressed. |

The bike sends a frame when the button state changes and may repeat a frame while a state is held. The bike does not acknowledge notifications, and a host does not need to reply.

## 4. Brake Value characteristic

The Brake Value value reports the position of the two brake levers.

| Octet | Field | Format | Presence |
|---|---|---|---|
| 0–1 | Left brake | `uint16` | Mandatory |
| 2–3 | Right brake | `uint16` | Mandatory |
| 4–19 | Additional values | `uint16` each | Optional; ignore |

A frame is 4 to 20 octets long. The bike sends two or more 16-bit values; a host reads the first two and ignores any remainder.

The value range is 0 to 1000:

- `0` means fully released.
- `1000` means fully applied.
- Intermediate values are linear.

The value is a raw lever position. It is not a force, a percentage, or a voltage. A host that wants a percentage can display `value / 10`.

The bike sends a frame when either lever changes and may repeat the current state. The two values are independent and require no calibration exchange.

## 5. Procedure

1. Connect to the bike and discover the primary service `0c46be5f-9c22-48ff-ae0e-c6eae1a2f4e5`.
2. Locate the Button Active and Brake Value characteristics.
3. Write `0x0001` to the CCCD of each characteristic.
4. Parse each notification using the layouts in sections 3 and 4.

The bike sends no notification before the CCCD write. The characteristics themselves are neither readable nor writable.

## 6. Security

The service is defined with open access. A host does not need encryption, authentication, or bonding to subscribe. A bike may restrict subscriptions to a bonded peer; if it does, it should apply the same access policy to its control service.

The service carries physical input state only. No value in it changes the bike's behaviour.
