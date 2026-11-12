"""WFTNP frame codec.

WFTNP tunnels BLE GATT operations over TCP. A frame is a 6-byte big-endian
header followed by an optional body, and UUIDs travel as 16 raw bytes in
canonical text order. This module is pure: it knows nothing about sockets or
services.
"""

from __future__ import annotations

import asyncio
import struct
from dataclasses import dataclass
from enum import IntEnum, IntFlag
from typing import Final
from uuid import UUID

VERSION: Final = 1
MAX_FRAME: Final = 4096
UUID_SIZE: Final = 16
_HEADER: Final = struct.Struct(">BBBBH")


class MessageType(IntEnum):
    """Frame type; a response repeats the request type."""

    DISCOVER_SERVICES = 0x01
    DISCOVER_CHARACTERISTICS = 0x02
    READ_CHARACTERISTIC = 0x03
    WRITE_CHARACTERISTIC = 0x04
    ENABLE_NOTIFICATIONS = 0x05
    CHARACTERISTIC_NOTIFICATION = 0x06
    ECHO = 0x07


class ResponseCode(IntEnum):
    """Status carried in the fourth header byte."""

    SUCCESS = 0x00
    UNKNOWN_MESSAGE_TYPE = 0x01
    UNEXPECTED_ERROR = 0x02
    SERVICE_NOT_FOUND = 0x03
    CHARACTERISTIC_NOT_FOUND = 0x04
    OPERATION_NOT_SUPPORTED = 0x05
    WRITE_FAILED = 0x06
    UNKNOWN_VERSION = 0x07


class Property(IntFlag):
    """Characteristic property bits reported by Discover Characteristics."""

    READ = 0x01
    WRITE = 0x02
    NOTIFY = 0x04


@dataclass(frozen=True, slots=True)
class Frame:
    """One WFTNP frame."""

    message_type: int
    sequence: int
    code: int = ResponseCode.SUCCESS
    body: bytes = b""
    version: int = VERSION

    def encode(self) -> bytes:
        """Serialise the frame, header first."""

        return _HEADER.pack(
            self.version,
            int(self.message_type),
            self.sequence,
            int(self.code),
            len(self.body),
        ) + self.body


def uuid_bytes(text: str) -> bytes:
    """Encode a UUID text form in canonical wire order."""

    return UUID(text).bytes


def uuid_text(raw: bytes) -> str:
    """Decode 16 canonical wire bytes back to text."""

    return str(UUID(bytes=raw))


async def read_frame(reader: asyncio.StreamReader) -> Frame:
    """Read one complete frame.

    Raises :class:`asyncio.IncompleteReadError` when the peer closes mid-frame
    and :class:`ValueError` when a declared body length breaks the frame limit;
    the caller cannot resynchronise the stream after the latter.
    """

    header = await reader.readexactly(_HEADER.size)
    version, message_type, sequence, code, length = _HEADER.unpack(header)
    if length > MAX_FRAME - _HEADER.size:
        raise ValueError(f"frame body of {length} bytes exceeds the protocol limit")
    body = await reader.readexactly(length) if length else b""
    return Frame(
        message_type=message_type,
        sequence=sequence,
        code=code,
        body=body,
        version=version,
    )


def response(
    request: Frame,
    code: ResponseCode = ResponseCode.SUCCESS,
    body: bytes = b"",
) -> bytes:
    """Build the response for ``request``: same type and sequence number."""

    return Frame(
        message_type=request.message_type,
        sequence=request.sequence,
        code=code,
        body=body,
    ).encode()


def notification(characteristic_uuid: str, value: bytes) -> bytes:
    """Build an unsolicited notification; its sequence number is unused."""

    return Frame(
        message_type=MessageType.CHARACTERISTIC_NOTIFICATION,
        sequence=0,
        body=uuid_bytes(characteristic_uuid) + value,
    ).encode()
