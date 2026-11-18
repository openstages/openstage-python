"""WFTNP server: BLE GATT services carried over TCP for local-network apps.

The package is independent of the rest of :mod:`openstage`. Import it (and
install the ``wftnp`` extra) only when a WFTNP endpoint is wanted; profiles
plug in through the plain data structures in :mod:`openstage.wftnp.server`.
"""

from __future__ import annotations


class WftnpDependencyError(ImportError):
    """The optional WFTNP dependencies are not installed."""


try:
    import zeroconf  # noqa: F401
    import zeroconf.asyncio  # noqa: F401
except ModuleNotFoundError as exc:
    raise WftnpDependencyError(
        "openstage.wftnp requires zeroconf; install it with "
        "'pip install openstage[wftnp]' or 'pip install zeroconf'"
    ) from exc

from .mdns import AdvertisementError

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
    WftnpStartError,
    preflight_port,
)

__all__ = [
    "AdvertisementError",
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
    "WftnpDependencyError",
    "WftnpServer",
    "WftnpStartError",
    "WriteRejected",
    "preflight_port",
    "elite_button",
    "notification",
    "read_frame",
    "response",
    "uuid_bytes",
    "uuid_text",
]
