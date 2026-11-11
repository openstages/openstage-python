# Bike notes

This page collects behaviour that matters when you work with an SB20 but does
not belong in the wire specifications. The specifications define the bytes;
these notes describe what the bike does with them.

## Connection

The SB20 exposes two Stages services: the control and telemetry service, and
the button/brake input service. The control service is the one that carries
requests and responses. The input service only reports physical controls.

The bike accepts one controller on the control service. If another application
holds it, the bike will not accept a new control connection. Close that
application before connecting. FTMS and CSC links are separate and do not give
you control.

Enabling notifications on the response characteristic is required before the
bike sends anything. A request written before that subscription may be lost.

One request may be outstanding at a time. Match a response by operation and
command, and do not send a second request while the first is waiting.

## Control modes

The most recent control request selects the mode:

| Mode | Request | Shifting |
|---|---|---|
| ERG | Target power (`0x02/0`) | Suppressed; the bike holds watts. |
| Simulation | Grade (`0x0F/0`) | Active. |
| Resistance | Level (`0x04/1`) | Active. |

There is no separate ERG-off command. Sending simulation grade 0.0 leaves ERG
and restores shifting, and resistance level 0 does the same. A target of zero is
still an ERG command for 0 W, not an exit.

ERG, simulation grade and resistance are live control states. They are not
persisted settings, and they are not written to flash.

## Persistence

A configuration Set updates RAM. It survives a disconnect and disappears after
a power cycle. The settings store (`0xFD/0`) writes the settings record to
flash. The record includes weight, button assignments, gradient scaling, crank
length, sensor configuration, brake and dual-BLE.

Two details matter:

- The bike sends `FD 04` (stored and applied) after an ordinary Set, without a
  store request. That message does not prove persistence. A store request starts
  with `FD 01`; `FD 03` is the stored confirmation.
- A store with an unchanged record performs no flash write.

## Drivetrain

A drivetrain transfer has two independent steps:

1. APPLY installs the map in RAM. It takes effect immediately.
2. STORE writes the applied map to flash.

APPLY carries the full transfer header again. The header contains the model,
front count, rear count, CRC, Dream Drive flag, step and map size, and only the
command octet changes to `0x0B`. The three-byte `0C 02 0B` form is not accepted.

The bike returns the stored header reliably, but the map body readback can be
incomplete. Treat the header as the reliable description of the stored model,
and use the preset tables to reconstruct a known map from its CRC and first
fragment. A map that is not in the bundled tables cannot be reconstructed.

A header read (`0C 02 07`) clears the applied transfer buffer. If you read the
header between APPLY and STORE, the store answers `STORE_INTERNAL_ERROR`; apply
the map again first.

The flash store is slow and becomes slower with repeated changed stores in one
power cycle. While a store runs, send no other control command, because a flash
read during the write can disturb it. Treat the store as busy until a terminal
reply arrives, and allow at least 240 seconds. A store request that is queued
answers `STORE_IN_PROGRESS` (`0x0A`); that is not a completion.

If a store never reports a terminal result, treat the flash contents as unknown
until you power-cycle and read the header. Do not repeat the store blindly.

## Calibration

Zero reset reports a global status, not a per-side status. A success frame
carries a mask that says which offsets are valid. The bike may send one frame
with only the left offset and a second frame with both offsets. Wait for both
offsets before treating calibration as complete.

There is no cancel or abort command. Once a reset starts, it runs to completion
or times out. A missing power meter may produce no response at all, so a host
timeout is required.

The left, right and torque offsets are opaque calibration coefficients. Their
physical unit is not defined.

## Buttons and brakes

The button characteristic reports 14 physical slots. Slot 19 exists only in the
button assignment array and enables the both-press front-toggle gesture. The
assignment array has 15 values in a fixed wire order that skips slots 4, 5, 9
and 10.

Assignment values 1–4 and 7–9 act as shift commands. Multishift values 20–23 are
stored but do not shift the drivetrain. Other values are stored and have no
effect.

The brake characteristic reports raw lever positions from 0 to 1000. It is not
a percentage, a force or a voltage. The brake enable flag in the parameters
operation controls the internal flywheel brake only; it does not affect lever
reporting.

## Ranges

| Value | Range | Wire unit |
|---|---|---|
| User and equipment weight | 0…655.35 kg | 0.01 kg |
| ERG target | 0…1000 W at the API | 0.5 W |
| Simulation grade | −20…+20 % | 0.001 % |
| Resistance level | 0…255 (0…100 %) | 1 level |
| Gradient scaling | 0…32767.5 % | 0.5 % |
| Crank length | 165, 170, 172.5 or 175 mm | 0.5 mm |
| Dream Drive gears | 10…50 | count |
| Dream Drive big shift | 0…9 | gears |
| Map fragment | 1…18 bytes | — |

The bike clamps ERG target power to its own limit. Reject values outside the
documented range before encoding; the bike's clamp is not a substitute for
validation.

## Measurement sources

Power, cadence, balance and left/right difference are available on the control
service. Speed and distance are available on FTMS, and CSC can provide cadence.
The values are not identical across sources: at the same instant the API and
FTMS power values decode to the same number, while cadence can differ by a few
rpm because the streams measure differently. Pick one source for a displayed
metric instead of mixing samples from different update instants.

FTMS Indoor Bike Data from the SB20 does not follow the standard field order.
Three shapes appear on the wire; decode only those and keep any other shape as
raw flags and bytes.

## Out-of-scope operations

Factory reset, UICR writes and firmware updates are outside the approved write
set. A host should not send them.
