"""GATT UUIDs used by the Stages SB20.

Stages UUIDs are written in full. Bluetooth SIG UUIDs are expanded to the
128-bit form used on the wire so comparisons against ``bleak`` need no special
casing. The button/brake service is separate from the control service.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Final, Mapping


def uuid16(value: int) -> str:
    """Expand a Bluetooth SIG 16-bit UUID to its 128-bit form."""

    return f"0000{value:04x}-0000-1000-8000-00805f9b34fb"


def normalize(value: str) -> str:
    """Normalise a UUID string for comparison (``bleak`` uses lowercase)."""

    return value.strip().lower()


# --------------------------------------------------------------------------- Stages
API_SERVICE: Final = "0c46beaf-9c22-48ff-ae0e-c6eae1a2f4e5"
API_REQUEST: Final = "0c46beb1-9c22-48ff-ae0e-c6eae1a2f4e5"
API_RESPONSE: Final = "0c46beb0-9c22-48ff-ae0e-c6eae1a2f4e5"

BUTTON_SERVICE: Final = "0c46be5f-9c22-48ff-ae0e-c6eae1a2f4e5"
BUTTON_ACTIVE: Final = "0c46be60-9c22-48ff-ae0e-c6eae1a2f4e5"
BRAKE_VALUE: Final = "0c46be61-9c22-48ff-ae0e-c6eae1a2f4e5"

#: Nordic Secure DFU. Present in normal mode; not used by this library.
DFU_SERVICE: Final = uuid16(0xFE59)
DFU_BUTTONLESS: Final = "8ec90003-f315-4f60-9fb8-838830daea50"

# --------------------------------------------------------------------------- SIG
GENERIC_ACCESS: Final = uuid16(0x1800)
GENERIC_ATTRIBUTE: Final = uuid16(0x1801)
DEVICE_INFORMATION: Final = uuid16(0x180A)
FITNESS_MACHINE: Final = uuid16(0x1826)
CYCLING_SPEED_AND_CADENCE: Final = uuid16(0x1816)
BATTERY_SERVICE: Final = uuid16(0x180F)

CCCD: Final = uuid16(0x2902)

DEVICE_NAME: Final = uuid16(0x2A00)
MODEL_NUMBER: Final = uuid16(0x2A24)
SERIAL_NUMBER: Final = uuid16(0x2A25)
FIRMWARE_REVISION: Final = uuid16(0x2A26)
HARDWARE_REVISION: Final = uuid16(0x2A27)
SOFTWARE_REVISION: Final = uuid16(0x2A28)
MANUFACTURER_NAME: Final = uuid16(0x2A29)

FTMS_FEATURE: Final = uuid16(0x2ACC)
FTMS_INDOOR_BIKE_DATA: Final = uuid16(0x2AD2)
FTMS_CONTROL_POINT: Final = uuid16(0x2AD9)
FTMS_STATUS: Final = uuid16(0x2ADA)
CSC_MEASUREMENT: Final = uuid16(0x2A5B)

#: Device Information characteristics read on connect. Each read is optional.
DIS_CHARACTERISTICS: Final[Mapping[str, str]] = MappingProxyType(
    {
        "model": MODEL_NUMBER,
        "serial": SERIAL_NUMBER,
        "firmware": FIRMWARE_REVISION,
        "hardware": HARDWARE_REVISION,
        "software": SOFTWARE_REVISION,
        "manufacturer": MANUFACTURER_NAME,
    }
)
