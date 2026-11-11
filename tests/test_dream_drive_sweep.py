"""Dream Drive sweep: step jumps, direction and no-wrap regression."""

from __future__ import annotations

from pathlib import Path

from openstage import pdu
from openstage.capture import Capture, load_capture
from openstage.pdu import Stream

FIXTURE = Path(__file__).parent / "fixtures" / "live" / "dream-drive-sweep.json"


def _rear_sequence(capture: Capture) -> list[int]:
    sequence: list[int] = []
    for frame in capture.frames:
        if frame.stream is not Stream.API:
            continue
        record = pdu.decode_pdu(frame.raw)
        if isinstance(record, pdu.GearNotification):
            if not sequence or sequence[-1] != record.rear_index:
                sequence.append(record.rear_index)
    return sequence


def test_dream_drive_sweep_matches_absolute_index_semantics() -> None:
    capture = load_capture(FIXTURE)
    header = next(
        record
        for frame in capture.frames
        if frame.stream is Stream.API
        for record in (pdu.decode_pdu(frame.raw),)
        if isinstance(record, pdu.GearModelHeader)
    )
    assert isinstance(header, pdu.GearModelHeader)
    assert header.model is pdu.ModelType.COEFFICIENTS
    assert (header.front_count, header.rear_count) == (1, 24)
    assert header.dream_drive is True
    assert header.dream_drive_step == 2
    assert header.crc8 == 0x47
    assert header.map_size == 48

    sequence = _rear_sequence(capture)
    assert sequence
    assert set(sequence) <= set(range(1, 25))
    assert max(sequence) == 23

    # The chainring-direction test at the end: +2, +2, then -2 repeatedly.
    assert sequence[-7:] == [8, 10, 12, 10, 8, 6, 4]

    # All transitions are one gear or the configured step; no wrap.
    legal = {-2, -1, 1, 2}
    assert all((b - a) in legal for a, b in zip(sequence, sequence[1:]))
    assert any(b - a == 2 for a, b in zip(sequence, sequence[1:]))
    assert any(b - a == -2 for a, b in zip(sequence, sequence[1:]))


def test_dream_drive_notifications_keep_front_index_one() -> None:
    capture = load_capture(FIXTURE)
    gears = [
        pdu.decode_pdu(frame.raw)
        for frame in capture.frames
        if frame.stream is Stream.API and frame.raw[:2] == b"\x0c\x01"
    ]
    assert gears
    assert all(isinstance(record, pdu.GearNotification) for record in gears)
    assert all(record.front_index == 1 for record in gears if isinstance(record, pdu.GearNotification))
    assert all(record.model is pdu.ModelType.COEFFICIENTS for record in gears if isinstance(record, pdu.GearNotification))
