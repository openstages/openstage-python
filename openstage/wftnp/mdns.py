"""mDNS advertisement for a WFTNP endpoint.

Clients discover controllers through ``_wahoo-fitness-tnp._tcp`` records. The
``zeroconf`` package owns the multicast plumbing; this module builds the
service record and ties its lifetime to the server.
"""

from __future__ import annotations

import logging
import re
import socket
from collections.abc import Mapping

from zeroconf import ServiceInfo
from zeroconf.asyncio import AsyncZeroconf

LOGGER = logging.getLogger(__name__)

SERVICE_TYPE = "_wahoo-fitness-tnp._tcp.local."


def local_ipv4() -> str:
    """Return the address of the interface that carries the default route.

    A UDP ``connect`` sends no packet, so this works offline. Loopback is only
    a fallback for local testing; the client must reach the advertised address
    over the LAN.
    """

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return str(sock.getsockname()[0])
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


def _dns_label(text: str) -> str:
    label = re.sub(r"[^a-z0-9-]+", "-", text.lower()).strip("-")
    return label or "wftnp"


class Advertisement:
    """A registered WFTNP mDNS service."""

    def __init__(
        self,
        *,
        name: str,
        port: int,
        txt: Mapping[str, str],
        host: str | None = None,
    ) -> None:
        self.name = name
        self.port = port
        self.txt = dict(txt)
        self.host = host
        self._zeroconf: AsyncZeroconf | None = None
        self._info: ServiceInfo | None = None

    async def start(self) -> None:
        """Publish the record and keep the responder alive until :meth:`stop`."""

        address = self.host or local_ipv4()
        self._info = ServiceInfo(
            type_=SERVICE_TYPE,
            name=f"{self.name}.{SERVICE_TYPE}",
            port=self.port,
            properties=self.txt,
            server=f"{_dns_label(self.name)}.local.",
            addresses=[socket.inet_aton(address)],
        )
        self._zeroconf = AsyncZeroconf()
        # The documented service label is longer than DNS-SD's 15-byte limit;
        # clients look up the literal name, so skip zeroconf's RFC check.
        await self._zeroconf.async_register_service(self._info, strict=False)
        LOGGER.info("advertised %s on %s:%d", self.name, address, self.port)

    async def stop(self) -> None:
        """Remove the record and close the responder."""

        zeroconf, info = self._zeroconf, self._info
        self._zeroconf = None
        self._info = None
        if zeroconf is None:
            return
        if info is not None:
            await zeroconf.async_unregister_service(info)
        await zeroconf.async_close()
