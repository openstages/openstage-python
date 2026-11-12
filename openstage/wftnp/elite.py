"""Elite Square profile: the status and button streams.

The profile is plain state plus two encoders. It never sends anything itself;
the owner calls :meth:`EliteSquare.encode_status` or
:meth:`EliteSquare.encode_buttons` after a change returns ``True`` and forwards
the bytes through the WFTNP server.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

from ..pdu import BUTTON_MASK_SLOTS
from .protocol import Property
from .server import GattCharacteristic, GattService

SERVICE = "347b0001-7635-408b-8918-8ff3949ce592"
STATUS = "347b0043-7635-408b-8918-8ff3949ce592"
BUTTONS = "347b0045-7635-408b-8918-8ff3949ce592"

#: Number of counters in one button page; the Elite client defines twenty.
BUTTON_COUNT = 20

#: Elite numbers by physical SB20 position. The two action keys take the low
#: navigation numbers because the app only offers those for remapping; the
#: buttons they displace take the campagnolo numbers. The app remaps in its own
#: UI, so positions stay stable across sessions.
_BUTTON_ROUTES = {
    1: 9,
    2: 10,
    3: 1,
    4: 19,
    5: 20,
    6: 2,
    7: 8,
    8: 18,
    9: 3,
    10: 4,
    11: 11,
    12: 12,
    13: 13,
    14: 14,
}

LEFT_BRAKE = 7
RIGHT_BRAKE = 17


def elite_button(physical: int) -> int:
    """Map an SB20 physical button to its Elite button number by position."""

    try:
        return _BUTTON_ROUTES[physical]
    except KeyError:
        raise ValueError(f"physical button {physical} has no Elite mapping") from None


@dataclass(frozen=True, slots=True)
class SequentialGear:
    """Dream Drive shifting: a gear number and an informational ratio."""

    total: int
    ratio: int
    current: int

    def __post_init__(self) -> None:
        _check_range("total gears", self.total, 1, 255)
        _check_range("ratio", self.ratio, 0, 0xFFFF)
        _check_range("current gear", self.current, 0, self.total)


@dataclass(frozen=True, slots=True)
class DerailleurGear:
    """Front and rear gear geometry."""

    front_count: int
    rear_count: int
    front_teeth: int
    rear_teeth: int
    current_front: int = 1
    current_rear: int = 1

    def __post_init__(self) -> None:
        _check_range("front count", self.front_count, 1, 255)
        _check_range("rear count", self.rear_count, 1, 255)
        _check_range("front teeth", self.front_teeth, 1, 255)
        _check_range("rear teeth", self.rear_teeth, 1, 255)
        _check_range("current front", self.current_front, 0, self.front_count)
        _check_range("current rear", self.current_rear, 0, self.rear_count)


def _check_range(label: str, value: int, low: int, high: int) -> None:
    if not low <= value <= high:
        raise ValueError(f"{label} must be {low}..{high}: {value!r}")


class EliteSquare:
    """The Elite Square service: one status value and one button page."""

    def __init__(
        self,
        *,
        active_profile: int = 1,
        battery: int = 100,
        gear: SequentialGear | DerailleurGear | None = None,
    ) -> None:
        _check_range("active profile", active_profile, 0, 255)
        _check_range("battery", battery, 0, 100)
        self._profile = active_profile
        self._battery = battery
        self._gear: SequentialGear | DerailleurGear = gear or DerailleurGear(
            front_count=1, rear_count=12, front_teeth=34, rear_teeth=28
        )
        self._counters = [0] * BUTTON_COUNT
        self._pressed: set[int] = set()
        self._physical_mask = 0
        self._service = GattService(
            SERVICE,
            (
                GattCharacteristic(
                    STATUS,
                    Property.READ | Property.NOTIFY,
                    read=self.encode_status,
                ),
                GattCharacteristic(
                    BUTTONS,
                    Property.READ | Property.NOTIFY,
                    read=self.encode_buttons,
                ),
            ),
        )

    @property
    def service(self) -> GattService:
        return self._service

    def set_battery(self, percent: int) -> bool:
        """Update the reported battery level; ``True`` when the value changed."""

        _check_range("battery", percent, 0, 100)
        if percent == self._battery:
            return False
        self._battery = percent
        return True

    def set_gear(self, gear: SequentialGear | DerailleurGear) -> bool:
        """Switch to a new gear state; ``True`` when the value changed."""

        if gear == self._gear:
            return False
        self._gear = gear
        return True

    def set_buttons(self, mask: int) -> bool:
        """Apply the SB20 button-active mask: bit N is physical button N+1.

        Returns ``True`` when at least one Elite counter changed.
        """

        if mask == self._physical_mask:
            return False
        changed = False
        for physical in range(1, len(BUTTON_MASK_SLOTS) + 1):
            pressed = bool(mask & (1 << (physical - 1)))
            if pressed == bool(self._physical_mask & (1 << (physical - 1))):
                continue
            changed = self._set(elite_button(physical), pressed) or changed
        self._physical_mask = mask & 0x3FFF
        return changed

    def press(self, button: int) -> bool:
        """Set a button bit; ``True`` when its counter changed."""

        return self._set(button, True)

    def release(self, button: int) -> bool:
        """Clear a button bit; ``True`` when its counter changed."""

        return self._set(button, False)

    def reset(self) -> None:
        """Return every counter and bit to the released state."""

        self._counters = [0] * BUTTON_COUNT
        self._pressed.clear()
        self._physical_mask = 0

    def encode_status(self) -> bytes:
        """Encode the 12-byte sequential or 14-byte derailleur status value."""

        pressed = 0
        for button in self._pressed:
            pressed |= 1 << (button - 1)
        gear = self._gear
        head = bytes([int(isinstance(gear, SequentialGear)), self._profile, self._battery])
        head += struct.pack(">I", pressed) + b"\x00"
        if isinstance(gear, SequentialGear):
            return head + bytes(
                [gear.total, gear.ratio >> 8, gear.ratio & 0xFF, gear.current]
            )
        return head + bytes(
            [
                gear.front_count,
                gear.rear_count,
                gear.front_teeth,
                gear.rear_teeth,
                gear.current_front,
                gear.current_rear,
            ]
        )

    def encode_buttons(self) -> bytes:
        """Encode button page 0: twenty 4-bit counters, low nibble first.

        The value is a full-state snapshot: the client diffs bit 0 against the
        previous frame and ignores the upper counter bits, so only an edge is
        meaningful. The first frame after subscribing becomes its baseline.
        """

        page = bytearray(1 + BUTTON_COUNT // 2)
        for index, counter in enumerate(self._counters):
            page[1 + index // 2] |= counter << (4 * (index % 2))
        return bytes(page)

    def _set(self, button: int, pressed: bool) -> bool:
        _check_range("Elite button", button, 1, BUTTON_COUNT)
        if pressed == (button in self._pressed):
            return False
        if pressed:
            self._pressed.add(button)
        else:
            self._pressed.discard(button)
        counter = (self._counters[button - 1] + 1) & 0x0F
        self._counters[button - 1] = (counter & 0x0E) | int(pressed)
        return True
