"""WFTNP server: BLE GATT services carried over TCP for local-network apps.

The package is independent of the rest of :mod:`openstage`. Import it (and
install the ``wftnp`` extra) only when a WFTNP endpoint is wanted; profiles
plug in through the plain data structures in :mod:`openstage.wftnp.server`.
"""

from __future__ import annotations

from .elite import (
    BUTTONS,
    SERVICE,
    STATUS,
    DerailleurGear,
    EliteSquare,
    SequentialGear,
    elite_button,
)
from .bridge import DEFAULT_MAC, DEFAULT_NAME, DEFAULT_SERIAL, WftnpBridge
from .ftms import FEATURE_VALUE, FtmsProxy
from .protocol import (
    MAX_FRAME,
    VERSION,
    Frame,
    MessageType,
    Property,
    ResponseCode,
    notification,
    read_frame,
    response,
    uuid_bytes,
    uuid_text,
)
from .server import (
    DEFAULT_PORT,
    GattCharacteristic,
    GattService,
    WriteRejected,
    WftnpServer,
)

__all__ = [
    "BUTTONS",
    "DEFAULT_MAC",
    "DEFAULT_NAME",
    "DEFAULT_PORT",
    "DEFAULT_SERIAL",
    "DerailleurGear",
    "EliteSquare",
    "FEATURE_VALUE",
    "Frame",
    "FtmsProxy",
    "GattCharacteristic",
    "GattService",
    "MAX_FRAME",
    "MessageType",
    "Property",
    "ResponseCode",
    "SERVICE",
    "STATUS",
    "SequentialGear",
    "VERSION",
    "WftnpBridge",
    "WftnpServer",
    "WriteRejected",
    "elite_button",
    "notification",
    "read_frame",
    "response",
    "uuid_bytes",
    "uuid_text",
]
