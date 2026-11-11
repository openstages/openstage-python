"""Cross-stream consistency test for API and FTMS power and cadence.

The fixture contains samples from both streams taken together. The pairing and
statistics logic stays in the test suite; it is not part of the library.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from pathlib import Path

from openstage import pdu
from openstage.capture import CapturedFrame, load_capture
from openstage.pdu import Stream

FIXTURE = Path(__file__).parent / "fixtures" / "live" / "live-ride.json"


@dataclass(frozen=True, slots=True)
class Pair:
    t_api: float
    t_ftms: float
    api_power_w: int
    ftms_power_w: int
    api_cadence_rpm: int
    ftms_cadence_rpm: float


def pair_api_ftms(frames: tuple[CapturedFrame, ...], max_skew_s: float = 0.25) -> list[Pair]:
    api: list[tuple[float, int, int]] = []
    ftms: list[tuple[float, int, float]] = []
    for frame in frames:
        if frame.stream is Stream.API and frame.raw[:2] == b"\x08\x02":
            record = pdu.decode_pdu(frame.raw)
            if (
                isinstance(record, pdu.PowerData)
                and record.instantaneous_w is not None
                and record.cadence_rpm is not None
            ):
                api.append((frame.t, record.instantaneous_w, record.cadence_rpm))
        elif frame.stream is Stream.FTMS_DATA and frame.raw[:2] == b"\xc5\x00" and len(frame.raw) == 8:
            record = pdu.decode_ftms_indoor_bike_data(frame.raw)
            if record.power_w is not None and record.cadence_rpm is not None:
                ftms.append((frame.t, record.power_w, record.cadence_rpm))

    pairs: list[Pair] = []
    for t_ftms, ftms_power, ftms_cadence in ftms:
        nearest = min(api, key=lambda sample: abs(sample[0] - t_ftms))
        if abs(nearest[0] - t_ftms) <= max_skew_s:
            pairs.append(
                Pair(
                    t_api=nearest[0],
                    t_ftms=t_ftms,
                    api_power_w=nearest[1],
                    ftms_power_w=ftms_power,
                    api_cadence_rpm=nearest[2],
                    ftms_cadence_rpm=ftms_cadence,
                )
            )
    return pairs


def test_live_api_and_ftms_power_cadence_pairing() -> None:
    capture = load_capture(FIXTURE)
    pairs = pair_api_ftms(capture.frames)

    assert len(pairs) == 84
    power_deltas = [pair.ftms_power_w - pair.api_power_w for pair in pairs]
    cadence_deltas = [pair.ftms_cadence_rpm - pair.api_cadence_rpm for pair in pairs]

    # Same-instant samples decode to the same power. The cadence difference is
    # a source difference between the API and the non-standard FTMS field.
    assert max(abs(delta) for delta in power_deltas) == 0
    assert statistics.median(cadence_deltas) == 3.0
    assert max(abs(delta) for delta in cadence_deltas) <= 14.0
