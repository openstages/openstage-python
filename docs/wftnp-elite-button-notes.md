# Elite Square button stream, client quirks

Observed against Rouvy, 2026-11-12. This supplements the button sections of
`wftnp-elite-spec.md`; it records how the client treats them, which the
specification does not state.

## The page is a snapshot

`347B0045` carries the current level of all twenty buttons, not an event. The
client stores the previous frame, diffs **bit 0 only**, and discards the upper
three bits of each nibble. A frame whose bit-0 pattern matches the previous one
is a no-op, whatever the counter values. Counter increments alone never trigger
an action.

Consequences:

Notify only when at least one bit 0 changes, and always send the release
frame. Never synthesize repeats or press/release click cycles: the client
repeats by itself, and extra frames flood it. A periodic snapshot at about 1 Hz
while held is safe, because identical snapshots are no-ops and a correct one
clears a stale held state after a lost release.

## Baseline after subscribe

The first value seen after subscribing is stored as a baseline and produces no
action. Every button that is down in that baseline is ignored until it is
released and pressed again; the release of a baseline-pressed button is also
swallowed.

The initial value must be an 11-byte page with page byte `0x00`. All counters
zero is valid. Read and notification are treated identically, so an all-zero
page can serve as both.

## Which edge fires depends on the binding

The adapter has no press-only or release-only bias. The bound command decides:

| Command group | Fires on |
|---|---|
| Increase, Decrease, Navigate up/down/left/right | `0 → 1` (press) |
| Select, Back, Screenshot, Pause/Resume, Hide interface, Skip segment, Toggle ERG, Kudos, Create lap, Chat | `1 → 0` (release) |
| Buttons with no bound command | neither |

This is why a pause binding appears to act on release while Increase acts on
press. It is not a polarity problem.

## Client-side repeat

Repeat exists only for press-and-repeat commands. After the press that starts
an action, the client waits 1000 ms and then repeats every 200 ms while the
button stays down. The hold state advances on the client's clock; one press
frame is enough to start it.

## Status mask

`347B0043` carries a 20-bit pressed mask with `1 = pressed`, but the adapter
does not consult it for button actions. It is used for gear and informational
state. There is no ordering requirement between status and button frames.

## Observations on Rouvy behaviour

- Navigation bindings repeat reliably from the snapshot stream.
- ERG +/− repeat worked only when Rouvy accepted the advertised device shape.
  The exact conditions are unresolved; the same bindings and frames behaved
  differently across sessions.
- If an Increase binding acts on release, suspect the binding or the advertised
  device shape before changing the encoder. Changing bit polarity or counter
  semantics did not fix it and introduced worse behaviour.
