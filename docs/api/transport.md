# `openstage.transport`

The transport layer is a narrow GATT boundary. The client talks to a `Transport`
and never imports bleak directly, which keeps protocol tests independent of a
radio.

## `Transport`

```python
@runtime_checkable
class Transport(Protocol):
    @property
    def is_connected(self) -> bool: ...
    @property
    def mtu_size(self) -> int | None: ...
    @property
    def service_uuids(self) -> frozenset[str]: ...
    async def connect(self) -> None: ...
    async def disconnect(self) -> None: ...
    async def read_characteristic(self, uuid: str) -> bytes: ...
    async def write_characteristic(self, uuid: str, data: bytes) -> None: ...
    async def start_notify(self, uuid: str, callback: NotifyCallback) -> None: ...
    async def stop_notify(self, uuid: str) -> None: ...
```

`NotifyCallback` is `Callable[[bytes], None]`. A transport must deliver each
notification value as one `bytes` object and must not reassemble or decode it.

## `BikeAdvertisement`

```python
BikeAdvertisement(
    name: str,
    address: str,
    rssi: int | None,
    service_uuids: frozenset[str],
)
```

One advertising device that passed the SB20 filter.

## Discovery

### `is_sb20_advertisement(name, service_uuids) -> bool`

True when the advertised name starts with `Stages Bike` and the advertisement
includes the FTMS service (`0x1826`). A Stages power meter also uses a name
beginning with `Stages`, but it does not advertise FTMS, so the service UUID is
the discriminator.

### `await discover_bikes(timeout=5.0, name_filter=None) -> list[BikeAdvertisement]`

Scans until the first matching advertisement appears and returns a one-element
list. Each attempt lasts `timeout` seconds; if nothing matches, scanning repeats
until it does. A `name_filter` is a case-insensitive substring of the advertised
name.

## `BleakTransport`

```python
BleakTransport(address_or_device, *, timeout=30.0)
```

A `Transport` backed by bleak's `BleakClient`.

| Member | Behaviour |
|---|---|
| `address` | The address string used for the connection. |
| `is_connected` | Reflects the bleak client state. |
| `mtu_size` | Returns the bleak client MTU when available. |
| `service_uuids` | Lowercase service UUIDs from the discovered GATT tree. |
| `connect()` / `disconnect()` | Delegate to bleak. |
| `read_characteristic(uuid)` | Returns the value as `bytes`. |
| `write_characteristic(uuid, data)` | Writes with `response=False`, matching the request characteristic. |
| `start_notify(uuid, callback)` | Registers a callback and starts notifications. |
| `stop_notify(uuid)` | Stops notifications and removes the callback. |

To test the client without a radio, implement `Transport` with a fake that
returns scripted responses from `write_characteristic()`.
