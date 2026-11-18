"""A WFTNP GATT server.

Profiles describe their services as data and supply read/write handlers;
this module owns the TCP listener, the request loop and the notification
plumbing. Profiles never touch sockets.
"""

from __future__ import annotations

import asyncio
import logging
import socket
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Final, TYPE_CHECKING

from .protocol import (
    UUID_SIZE,
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

if TYPE_CHECKING:
    from .mdns import Advertisement

LOGGER = logging.getLogger(__name__)

DEFAULT_PORT: Final = 36866


class WftnpStartError(RuntimeError):
    """The WFTNP endpoint cannot be started on this host."""


async def _reject_connection(
    _reader: asyncio.StreamReader, writer: asyncio.StreamWriter
) -> None:
    writer.close()
    await writer.wait_closed()


async def preflight_port(preferred: int = DEFAULT_PORT) -> int:
    """Verify that a TCP listener can bind, returning the chosen port."""

    last_error: BaseException | None = None
    for candidate in dict.fromkeys((preferred, 0)):
        try:
            server = await asyncio.start_server(
                _reject_connection, host="0.0.0.0", port=candidate
            )
        except OSError as exc:
            last_error = exc
            continue
        try:
            sockets = server.sockets or ()
            return int(sockets[0].getsockname()[1])
        finally:
            server.close()
            await server.wait_closed()
    raise WftnpStartError(
        f"cannot bind 0.0.0.0:{preferred} or an ephemeral fallback: {last_error}"
    )


ReadHandler = Callable[[], bytes]
WriteHandler = Callable[[bytes], Awaitable[None]]
SessionHandler = Callable[[], Awaitable[None]]


class WriteRejected(Exception):
    """Raised by a write handler when the value cannot be accepted."""


@dataclass(frozen=True, slots=True)
class GattCharacteristic:
    """One characteristic exposed through WFTNP."""

    uuid: str
    properties: Property
    read: ReadHandler | None = None
    write: WriteHandler | None = None


@dataclass(frozen=True, slots=True)
class GattService:
    """One service exposed through WFTNP."""

    uuid: str
    characteristics: tuple[GattCharacteristic, ...]


class WftnpServer:
    """Serve a GATT tree over TCP and advertise it over mDNS.

    One client is served at a time; a second connection replaces the first,
    which the transport specification allows. ``on_disconnect`` runs after the
    last frame of a session, so profiles can reset per-session state.
    """

    def __init__(
        self,
        *,
        name: str,
        services: Sequence[GattService],
        txt: Mapping[str, str],
        port: int = DEFAULT_PORT,
        host: str | None = None,
        advertise: bool = True,
        on_connect: SessionHandler | None = None,
        on_disconnect: SessionHandler | None = None,
    ) -> None:
        self.name = name
        self.port = port
        self.txt = dict(txt)
        self.host = host
        self.advertise = advertise
        self.on_connect = on_connect
        self.on_disconnect = on_disconnect
        self._services = {service.uuid.lower(): service for service in services}
        self._characteristics = {
            characteristic.uuid.lower(): characteristic
            for service in services
            for characteristic in service.characteristics
        }
        self._server: asyncio.AbstractServer | None = None
        self._advertisement: Advertisement | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._send_lock = asyncio.Lock()
        self._subscriptions: set[str] = set()

    @property
    def connected(self) -> bool:
        return self._writer is not None

    async def start(self) -> None:
        """Bind the listener and publish the mDNS record."""

        self._server = await asyncio.start_server(
            self._handle_client, host="0.0.0.0", port=self.port
        )
        sockets = self._server.sockets
        if sockets:
            self.port = int(sockets[0].getsockname()[1])
        if not self.advertise:
            return
        # Imported here so the package works without the optional zeroconf
        # dependency until a caller actually advertises.
        from .mdns import Advertisement

        self._advertisement = Advertisement(
            name=self.name, port=self.port, txt=self.txt, host=self.host
        )
        try:
            await self._advertisement.start()
        except Exception:
            await self.stop()
            raise

    async def stop(self) -> None:
        """Close the client, the listener and the advertisement."""

        await self._drop_client()
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        if self._advertisement is not None:
            await self._advertisement.stop()
            self._advertisement = None

    async def notify(self, characteristic_uuid: str, value: bytes) -> None:
        """Send a notification, unless the client has not enabled this one."""

        if characteristic_uuid.lower() not in self._subscriptions:
            return
        await self._send(notification(characteristic_uuid, value))

    async def _drop_client(self) -> None:
        writer, self._writer = self._writer, None
        if writer is None:
            return
        writer.close()
        try:
            await writer.wait_closed()
        except (ConnectionError, OSError):
            pass

    async def _send(self, data: bytes) -> None:
        writer = self._writer
        if writer is None:
            return
        LOGGER.debug("%s -> %s", self.name, data.hex())
        async with self._send_lock:
            if self._writer is not writer:
                return
            try:
                writer.write(data)
                await writer.drain()
            except (ConnectionError, OSError):
                LOGGER.debug("dropping write to a closed WFTNP peer", exc_info=True)

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        await self._drop_client()
        self._writer = writer
        self._subscriptions.clear()
        sock = writer.get_extra_info("socket")
        if sock is not None:
            try:
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            except OSError:
                LOGGER.debug("cannot set TCP socket options", exc_info=True)
        LOGGER.info(
            "%s: client connected from %s",
            self.name,
            writer.get_extra_info("peername"),
        )
        try:
            if self.on_connect is not None:
                await self.on_connect()
            while True:
                await self._handle_frame(await read_frame(reader))
        except asyncio.IncompleteReadError:
            LOGGER.info("%s: client closed the connection", self.name)
        except (ConnectionError, OSError):
            LOGGER.info("%s: connection lost", self.name)
        except ValueError as exc:
            LOGGER.warning("%s: closing malformed connection: %s", self.name, exc)
        finally:
            if self._writer is writer:
                self._subscriptions.clear()
                self._writer = None
                if self.on_disconnect is not None:
                    await self.on_disconnect()
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionError, OSError):
                pass

    async def _handle_frame(self, frame: Frame) -> None:
        LOGGER.debug(
            "%s <- type=%d seq=%d code=%d body=%s",
            self.name,
            frame.message_type,
            frame.sequence,
            frame.code,
            frame.body.hex(),
        )
        if frame.version != VERSION:
            await self._send(response(frame, ResponseCode.UNKNOWN_VERSION))
            return
        try:
            match frame.message_type:
                case MessageType.DISCOVER_SERVICES:
                    await self._send(
                        response(
                            frame,
                            body=b"".join(
                                uuid_bytes(service.uuid)
                                for service in self._services.values()
                            ),
                        )
                    )
                case MessageType.DISCOVER_CHARACTERISTICS:
                    await self._discover_characteristics(frame)
                case MessageType.READ_CHARACTERISTIC:
                    await self._read_characteristic(frame)
                case MessageType.WRITE_CHARACTERISTIC:
                    await self._write_characteristic(frame)
                case MessageType.ENABLE_NOTIFICATIONS:
                    await self._enable_notifications(frame)
                case MessageType.ECHO:
                    await self._send(response(frame))
                case _:
                    await self._send(
                        response(frame, ResponseCode.UNKNOWN_MESSAGE_TYPE)
                    )
        except Exception:
            LOGGER.exception("%s: request failed", self.name)
            await self._send(response(frame, ResponseCode.UNEXPECTED_ERROR))

    def _characteristic(self, body: bytes) -> GattCharacteristic | None:
        return self._characteristics.get(uuid_text(body[:UUID_SIZE]).lower())

    async def _discover_characteristics(self, frame: Frame) -> None:
        if len(frame.body) != UUID_SIZE:
            await self._send(response(frame, ResponseCode.UNEXPECTED_ERROR))
            return
        service = self._services.get(uuid_text(frame.body).lower())
        if service is None:
            await self._send(response(frame, ResponseCode.SERVICE_NOT_FOUND))
            return
        body = bytearray(uuid_bytes(service.uuid))
        for characteristic in service.characteristics:
            body.extend(uuid_bytes(characteristic.uuid))
            body.append(int(characteristic.properties))
        await self._send(response(frame, body=bytes(body)))

    async def _read_characteristic(self, frame: Frame) -> None:
        if len(frame.body) != UUID_SIZE:
            await self._send(response(frame, ResponseCode.UNEXPECTED_ERROR))
            return
        characteristic = self._characteristic(frame.body)
        if characteristic is None:
            await self._send(response(frame, ResponseCode.CHARACTERISTIC_NOT_FOUND))
            return
        if characteristic.read is None:
            await self._send(response(frame, ResponseCode.OPERATION_NOT_SUPPORTED))
            return
        value = characteristic.read()
        await self._send(
            response(frame, body=uuid_bytes(characteristic.uuid) + value)
        )

    async def _write_characteristic(self, frame: Frame) -> None:
        if len(frame.body) < UUID_SIZE:
            await self._send(response(frame, ResponseCode.UNEXPECTED_ERROR))
            return
        characteristic = self._characteristic(frame.body)
        if characteristic is None:
            await self._send(response(frame, ResponseCode.CHARACTERISTIC_NOT_FOUND))
            return
        if characteristic.write is None:
            await self._send(response(frame, ResponseCode.OPERATION_NOT_SUPPORTED))
            return
        try:
            await characteristic.write(frame.body[UUID_SIZE:])
        except WriteRejected:
            await self._send(response(frame, ResponseCode.WRITE_FAILED))
            return
        await self._send(response(frame, body=uuid_bytes(characteristic.uuid)))

    async def _enable_notifications(self, frame: Frame) -> None:
        if len(frame.body) <= UUID_SIZE:
            await self._send(response(frame, ResponseCode.UNEXPECTED_ERROR))
            return
        characteristic = self._characteristic(frame.body)
        if characteristic is None:
            await self._send(response(frame, ResponseCode.CHARACTERISTIC_NOT_FOUND))
            return
        if not characteristic.properties & Property.NOTIFY:
            await self._send(response(frame, ResponseCode.OPERATION_NOT_SUPPORTED))
            return
        # Clients may send a notify byte and an indicate byte; treat any
        # non-zero byte as "enable". The documented layout carries one byte.
        enabled = any(frame.body[UUID_SIZE:])
        if enabled:
            self._subscriptions.add(characteristic.uuid.lower())
        else:
            self._subscriptions.discard(characteristic.uuid.lower())
        await self._send(response(frame, body=uuid_bytes(characteristic.uuid)))
        if enabled and characteristic.read is not None:
            # Some clients subscribe without reading first, so one immediate
            # notification is the only way they learn the current value.
            await self._send(notification(characteristic.uuid, characteristic.read()))
