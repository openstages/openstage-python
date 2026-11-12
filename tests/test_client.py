"""Client transaction tests with a fake GATT transport."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

import pytest

from openstage import pdu, uuids
from openstage.client import (
    BikeProtocolError,
    BikeTimeout,
    ControlMode,
    DrivetrainMode,
    SB20Client,
    ZeroResetTimeout,
)
from openstage.transport import NotifyCallback

DIS_VALUES = {
    uuids.MODEL_NUMBER: b"SB20",
    uuids.SERIAL_NUMBER: b"H0603210071",
    uuids.FIRMWARE_REVISION: b"1.1",
    uuids.HARDWARE_REVISION: b"0.0",
    uuids.SOFTWARE_REVISION: b"1.12.4+3792",
    uuids.MANUFACTURER_NAME: b"Stages Cycling",
    uuids.DEVICE_NAME: b"Stages Bike 0071",
}


class FakeTransport:
    def __init__(self, responses: dict[bytes, list[bytes]] | None = None) -> None:
        self.responses = responses or {}
        self._connected = False
        self.writes: list[bytes] = []
        self.ftms_writes: list[bytes] = []
        self.callbacks: dict[str, Callable[[bytes], None]] = {}
        self.stopped: list[str] = []

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def mtu_size(self) -> int | None:
        return 23 if self._connected else None

    @property
    def service_uuids(self) -> frozenset[str]:
        return frozenset({uuids.API_SERVICE, uuids.FITNESS_MACHINE})

    async def connect(self) -> None:
        self._connected = True

    async def disconnect(self) -> None:
        self._connected = False

    async def read_characteristic(self, uuid: str) -> bytes:
        if uuid in DIS_VALUES:
            return DIS_VALUES[uuid]
        raise KeyError(uuid)

    async def write_characteristic(self, uuid: str, data: bytes) -> None:
        assert self._connected
        if uuid == uuids.FTMS_CONTROL_POINT:
            self.ftms_writes.append(data)
            return
        assert uuid == uuids.API_REQUEST
        self.writes.append(data)
        callback = self.callbacks.get(uuids.API_RESPONSE)
        for response in self.responses.get(data, []):
            if callback is not None:
                callback(response)

    async def start_notify(self, uuid: str, callback: NotifyCallback) -> None:
        self.callbacks[uuids.normalize(uuid)] = callback

    async def stop_notify(self, uuid: str) -> None:
        self.stopped.append(uuids.normalize(uuid))
        self.callbacks.pop(uuids.normalize(uuid), None)


def make_client(transport: FakeTransport, timeout: float = 0.2) -> SB20Client:
    return SB20Client(
        transport=transport,
        request_timeout=timeout,
        subscribe_buttons=False,
        subscribe_ftms=False,
        subscribe_csc=False,
    )


async def test_connect_reads_identity_and_status() -> None:
    transport = FakeTransport({b"\x0e\x00": [bytes.fromhex("0e000000000201")]})
    client = make_client(transport)

    await client.connect()
    assert client.mtu == 23
    assert client.identity.model == "SB20"
    assert client.identity.serial == "H0603210071"
    assert client.identity.software == "1.12.4+3792"
    assert client.identity.name == "Stages Bike 0071"

    status = await client.get_bike_status()
    assert status.flywheel_rpm == 0.0
    assert status.power_meter is pdu.SensorStatus.CONNECTED
    assert transport.writes == [b"\x0e\x00"]
    await client.close()
    assert transport.stopped == [uuids.API_RESPONSE]


async def test_error_pdu_raises() -> None:
    transport = FakeTransport({b"\x0e\x00": [bytes.fromhex("fe0e00")]})
    client = make_client(transport)
    await client.connect()
    with pytest.raises(BikeProtocolError):
        await client.get_bike_status()
    await client.close()


async def test_firmware_echo_response_completes_request() -> None:
    transport = FakeTransport(
        {b"\x0d\x02": [bytes.fromhex("0d0280a9312e382e32")]}
    )
    client = make_client(transport)
    await client.connect()
    firmware = await client.get_sensor_firmware("left")
    assert firmware.command == pdu.FirmwareCommand.GET_LEFT
    assert firmware.ant_id == 43392
    assert firmware.version == "1.8.2"
    await client.close()


async def test_timeout_raises() -> None:
    transport = FakeTransport()
    client = make_client(transport, timeout=0.01)
    await client.connect()
    with pytest.raises(BikeTimeout):
        await client.get_bike_status()
    await client.close()


async def test_power_event_does_not_satisfy_subscription_ack() -> None:
    event = bytes.fromhex("08021f50000000500000004d002401")
    transport = FakeTransport({b"\x08\x00": [event, b"\x08\x00"]})
    client = make_client(transport)
    await client.connect()

    await client.subscribe_power_data()
    assert "power" in client._telemetry
    await client.close()


async def test_query_all_keeps_per_query_failures() -> None:
    transport = FakeTransport(
        {
            b"\x0e\x00": [bytes.fromhex("0e000000000201")],
            b"\x0a\x00": [bytes.fromhex("0a014a0180a91e850a03aeb2c35b6d02")],
            b"\x10\x01": [bytes.fromhex("10010102")],
            b"\x04\x00": [bytes.fromhex("040000")],
            b"\x05\x00": [bytes.fromhex("0500640000")],
            b"\x03\x00": [bytes.fromhex("0300d0204006")],
            b"\x07\x00": [bytes.fromhex("070050")],
            b"\x0b\x01": [bytes.fromhex("0b01030405030405030404030102030400")],
            b"\x0c\x00\x02": [bytes.fromhex("0c0100010322001c000004")],
            b"\x0c\x02\x07": [bytes.fromhex("0c020800010c0f00040d00")],
            b"\x0d\x02": [bytes.fromhex("0d0a01020304050607")],
            b"\x0d\x04": [bytes.fromhex("0d0a01020304050607")],
        }
    )
    client = make_client(transport)
    await client.connect()
    results = await client.query_all()

    assert isinstance(results["bike_status"], pdu.BikeStatus)
    assert isinstance(results["power_meter_pairing"], pdu.PowerMeterPairing)
    assert isinstance(results["parameters"], pdu.Parameters)
    assert isinstance(results["resistance"], pdu.Resistance)
    assert isinstance(results["gradient_scaling"], pdu.GradientScaling)
    assert isinstance(results["weight"], pdu.Weight)
    assert isinstance(results["battery"], pdu.Battery)
    assert isinstance(results["buttons"], pdu.ButtonAssignments)
    assert isinstance(results["current_gear"], pdu.GearNotification)
    assert isinstance(results["gear_model"], pdu.GearModelHeader)
    assert isinstance(results["sensor_firmware_left"], pdu.SensorFirmware)
    assert isinstance(results["sensor_firmware_right"], pdu.SensorFirmware)
    assert len(results) == len(pdu.QUERY_REQUESTS)
    await client.close()


async def test_set_weight_without_store_sends_only_the_set() -> None:
    weight_response = bytes.fromhex("03013421ee02")
    transport = FakeTransport({bytes.fromhex("03013421ee02"): [weight_response]})
    client = make_client(transport)
    await client.connect()

    result = await client.set_weight(85.0, 7.5)
    assert isinstance(result.set_response, pdu.Weight)
    assert result.store is None
    assert result.persisted is False
    assert transport.writes == [bytes.fromhex("03013421ee02")]
    await client.close()


async def test_set_weight_store_waits_for_fd03_and_ignores_prior_fd04() -> None:
    weight_response = bytes.fromhex("03013421ee02")
    transport = FakeTransport(
        {
            bytes.fromhex("03013421ee02"): [
                weight_response,
                bytes.fromhex("fd04"),  # unsolicited apply from the Set
            ],
            bytes.fromhex("fd00"): [
                bytes.fromhex("fd01"),
                bytes.fromhex("fd03"),
                bytes.fromhex("fd04"),
            ],
        }
    )
    client = make_client(transport)
    await client.connect()

    result = await client.set_weight(85.0, 7.5, store=True)
    assert result.store is not None
    assert result.store.stored is True
    assert result.persisted is True
    assert [message.command for message in result.store.messages] == [1, 3]
    assert transport.writes == [bytes.fromhex("03013421ee02"), bytes.fromhex("fd00")]
    await client.close()


async def test_set_parameters_and_buttons() -> None:
    parameter_response = bytes.fromhex("10000100")
    button_values = [1, 2, 5, 3, 4, 5, 1, 2, 4, 3, 1, 2, 3, 4, 0]
    button_response = bytes.fromhex("0b00" + "".join(f"{value:02x}" for value in button_values))
    transport = FakeTransport(
        {
            bytes.fromhex("10000100"): [parameter_response],
            bytes.fromhex("0b00" + "".join(f"{value:02x}" for value in button_values)): [
                button_response
            ],
        }
    )
    client = make_client(transport)
    await client.connect()

    parameters = await client.set_parameters(True, None)
    assert isinstance(parameters.set_response, pdu.Parameters)
    buttons = await client.set_buttons(button_values)
    assert isinstance(buttons.set_response, pdu.ButtonAssignments)
    await client.close()


async def test_set_button_actions_semantic() -> None:
    actions = (pdu.ButtonAction.REAR_UP, pdu.ButtonAction.REAR_DOWN,
               pdu.ButtonAction.NOT_ASSIGNED) + (pdu.ButtonAction.NOT_ASSIGNED,) * 11
    request = pdu.build_set_button_actions(actions)
    transport = FakeTransport({request.pdu: [request.pdu]})
    client = make_client(transport)
    await client.connect()

    result = await client.set_button_actions(actions)
    assert isinstance(result.set_response, pdu.ButtonAssignments)
    assert transport.writes == [request.pdu]
    await client.close()


async def test_store_settings_busy_and_unchanged() -> None:
    busy_transport = FakeTransport(
        {bytes.fromhex("fd00"): [bytes.fromhex("fd01"), bytes.fromhex("fd02")]}
    )
    busy_client = make_client(busy_transport)
    await busy_client.connect()
    busy = await busy_client.store_settings()
    assert busy.busy is True
    assert busy.stored is False
    await busy_client.close()

    unchanged_transport = FakeTransport({bytes.fromhex("fd00"): [bytes.fromhex("fd04")]})
    unchanged_client = make_client(unchanged_transport)
    await unchanged_client.connect()
    unchanged = await unchanged_client.store_settings()
    assert unchanged.busy is False
    assert unchanged.stored is True
    await unchanged_client.close()


async def test_store_settings_timeout() -> None:
    transport = FakeTransport()
    client = SB20Client(transport=transport, store_timeout=0.01,
                        subscribe_buttons=False, subscribe_ftms=False,
                        subscribe_csc=False)
    await client.connect()
    with pytest.raises(BikeTimeout):
        await client.store_settings()
    await client.close()


async def test_close_unsubscribes_telemetry() -> None:
    transport = FakeTransport(
        {
            b"\x08\x00": [b"\x08\x00"],
            b"\x0c\x00\x01": [b"\x0c\x00\x01"],
            b"\x08\x01": [b"\x08\x01"],
            b"\x0c\x00\x00": [b"\x0c\x00\x00"],
        }
    )
    client = make_client(transport)
    await client.connect()
    await client.subscribe_power_data()
    await client.subscribe_gearing()
    await client.close()

    assert b"\x08\x01" in transport.writes
    assert b"\x0c\x00\x00" in transport.writes
    assert transport.is_connected is False


async def test_settings_builder_skips_unchanged_and_batches_changes() -> None:
    current_buttons = bytes.fromhex("0b01030405030405030404030102030400")
    weight_set = bytes.fromhex("030134214006")
    params_set = bytes.fromhex("10000000")
    buttons_set = bytes.fromhex("0b00010405030405030404030102030400")
    transport = FakeTransport(
        {
            b"\x03\x00": [bytes.fromhex("0300d0204006")],
            b"\x10\x01": [bytes.fromhex("10010102")],
            b"\x0b\x01": [current_buttons],
            weight_set: [weight_set],
            params_set: [params_set],
            buttons_set: [buttons_set],
        }
    )
    client = make_client(transport)
    await client.connect()

    unchanged = await (
        client.settings()
        .set_weight(84.0, 16.0)
        .set_brake(True)
        .set_button_slots({1: pdu.ButtonAction.REAR_UP})
        .commit()
    )
    assert unchanged.writes == 0
    assert weight_set not in transport.writes
    assert params_set not in transport.writes
    assert buttons_set not in transport.writes

    changed = await (
        client.settings()
        .set_weight(85.0, 16.0)
        .set_brake(False)
        .set_button_slots({1: pdu.ButtonAction.FRONT_UP})
        .commit()
    )
    assert changed.writes == 3
    assert changed.persisted is False
    assert weight_set in transport.writes
    assert params_set in transport.writes
    assert buttons_set in transport.writes
    assert b"\xfd\x00" not in transport.writes
    await client.close()


async def test_settings_builder_store_is_opt_in() -> None:
    weight_set = bytes.fromhex("030134214006")
    transport = FakeTransport(
        {
            b"\x03\x00": [bytes.fromhex("0300d0204006")],
            weight_set: [weight_set],
            b"\xfd\x00": [bytes.fromhex("fd01"), bytes.fromhex("fd03")],
        }
    )
    client = make_client(transport)
    await client.connect()

    result = await client.settings().set_weight(85.0, 16.0).commit(store=True)
    assert result.writes == 1
    assert result.persisted is True
    assert transport.writes[-1] == b"\xfd\x00"
    await client.close()


async def test_settings_builder_uses_physical_button_numbers() -> None:
    current_buttons = bytes.fromhex("0b01030405030405030404030102030400")
    buttons_set = bytes.fromhex("0b00030405040405030404030102030400")
    transport = FakeTransport(
        {
            b"\x0b\x01": [current_buttons],
            buttons_set: [buttons_set],
        }
    )
    client = make_client(transport)
    await client.connect()

    result = await client.settings().set_button_slots(
        {4: pdu.ButtonAction.REAR_DOWN}
    ).commit()
    assert result.writes == 1
    assert buttons_set in transport.writes
    await client.close()


async def test_control_and_config_client_methods() -> None:
    gradient = bytes.fromhex("0501c80000")
    resistance = bytes.fromhex("040180")
    crank = bytes.fromhex("0a035901")
    power = bytes.fromhex("02009001000000000000")
    grade = bytes.fromhex("0f008813000000000000")
    zero = bytes.fromhex("0601011ff6ff1400785634123412fbff")
    transport = FakeTransport(
        {
            gradient: [gradient],
            resistance: [resistance],
            crank: [crank],
            power: [power],
            grade: [grade],
            bytes.fromhex("0600"): [zero],
        }
    )
    client = make_client(transport)
    await client.connect()

    gradient_result = await client.set_gradient_scaling(200, False)
    assert isinstance(gradient_result.set_response, pdu.GradientScaling)
    resistance_result = await client.set_target_resistance(50.0)
    assert isinstance(resistance_result.set_response, pdu.Resistance)
    crank_result = await client.set_crank_length(172.5)
    assert isinstance(crank_result.set_response, pdu.PowerMeterPairing)
    assert crank_result.set_response.crank_length_mm == 172.5
    power_result = await client.set_target_power(200)
    assert isinstance(power_result.set_response, pdu.Power)
    assert power_result.set_response.power_w == 200
    grade_result = await client.set_target_grade(5.0)
    assert isinstance(grade_result.set_response, pdu.FtmsParameters)
    assert grade_result.set_response.grade_percent == 5.0
    zero_result = await client.zero_reset()
    assert zero_result.successful is True
    assert zero_result.left_offset == -10
    assert zero_result.right_offset == 20
    await client.close()


async def test_settings_builder_diffs_gradient_and_crank() -> None:
    gradient_set = bytes.fromhex("0501c80000")
    crank_set = bytes.fromhex("0a035901")
    transport = FakeTransport(
        {
            b"\x05\x00": [bytes.fromhex("0500640000")],
            b"\x0a\x00": [bytes.fromhex("0a014a0180a91e850a03aeb2c35b6d02")],
            gradient_set: [gradient_set],
            crank_set: [crank_set],
        }
    )
    client = make_client(transport)
    await client.connect()

    result = await (
        client.settings()
        .set_gradient_scaling(200, False)
        .set_crank_length(172.5)
        .commit()
    )
    assert result.writes == 2
    assert gradient_set in transport.writes
    assert crank_set in transport.writes
    await client.close()


async def test_erg_facade_on_and_off() -> None:
    power = bytes.fromhex("02009001000000000000")
    grade_zero = bytes.fromhex("0f000000000000000000")
    transport = FakeTransport({power: [power], grade_zero: [grade_zero]})
    client = make_client(transport)
    await client.connect()

    on = await client.erg_on(200)
    assert on.set_response.op == pdu.Op.POWER
    off = await client.erg_off()
    assert off.set_response.op == pdu.Op.FTMS_PARAMETERS
    assert transport.writes == [power, grade_zero]
    await client.close()


async def test_set_control_mode_routes_target() -> None:
    power = bytes.fromhex("02009001000000000000")
    resistance = bytes.fromhex("040180")
    grade = bytes.fromhex("0f008813000000000000")
    transport = FakeTransport({power: [power], resistance: [resistance], grade: [grade]})
    client = make_client(transport)
    await client.connect()

    await client.set_control_mode(ControlMode.ERG, 200)
    await client.set_control_mode(ControlMode.RESISTANCE, 50.0)
    await client.set_control_mode(ControlMode.SIMULATION, 5.0)
    assert transport.writes == [power, resistance, grade]
    await client.close()


async def test_set_target_resistance() -> None:
    resistance = bytes.fromhex("040180")
    transport = FakeTransport({resistance: [resistance]})
    client = make_client(transport)
    await client.connect()

    result = await client.set_target_resistance(50.0)
    assert isinstance(result.set_response, pdu.Resistance)
    assert result.set_response.level == 128
    assert transport.writes == [resistance]
    await client.close()


async def test_zero_reset_merges_left_then_right_offsets() -> None:
    left = bytes.fromhex("060101017e0300000000000000000000")
    both = bytes.fromhex("060101037e0355030000000000000000")
    transport = FakeTransport({b"\x06\x00": [left, both]})
    client = make_client(transport)
    await client.connect()

    result = await client.zero_reset()
    assert result.status is pdu.ZeroResetStatus.SUCCESS
    assert result.left_offset == 894
    assert result.right_offset == 853
    assert result.valid_mask & 0x03 == 0x03
    assert transport.writes == [b"\x06\x00"]
    await client.close()


async def test_zero_reset_preserves_success_side_on_global_error() -> None:
    left = bytes.fromhex("060101017e0300000000000000000000")
    error = bytes.fromhex("060102")
    transport = FakeTransport({b"\x06\x00": [left, error]})
    client = make_client(transport)
    await client.connect()

    result = await client.zero_reset()
    assert result.status is pdu.ZeroResetStatus.ERROR_NO_CONNECTION
    assert result.left_offset == 894
    assert result.right_offset is None
    await client.close()


async def test_zero_reset_timeout_keeps_partial_left_offset() -> None:
    left = bytes.fromhex("060101017e0300000000000000000000")
    transport = FakeTransport({b"\x06\x00": [left]})
    client = SB20Client(
        transport=transport,
        request_timeout=0.2,
        zero_reset_timeout=0.05,
        subscribe_buttons=False,
        subscribe_ftms=False,
        subscribe_csc=False,
    )
    await client.connect()

    with pytest.raises(ZeroResetTimeout) as caught:
        await client.zero_reset()
    assert caught.value.result.left_offset == 894
    assert caught.value.result.right_offset is None
    await client.close()


async def test_gearing_commit_applies_ram_only() -> None:
    transport = FakeTransport()
    client = make_client(transport)
    await client.connect()
    plan = client.gears().set_gears_number(24).set_front_shift(2).plan()
    grade = bytes.fromhex("0f000000000000000000")
    transport.responses[grade] = [grade]
    transport.responses[plan.header.pdu] = [bytes.fromhex("0c0201")]
    transport.responses[plan.apply.pdu] = [bytes.fromhex("0c020c")]

    result = await client.gears().set_gears_number(24).set_front_shift(2).commit()

    assert result.request_ack.command == 1
    assert result.apply_ack.command == 12
    assert plan.apply.pdu == bytes.fromhex("0c020b0201184701023000")
    assert transport.writes == [
        grade,
        plan.header.pdu,
        *(fragment.pdu for fragment in plan.fragments),
        plan.apply.pdu,
    ]
    assert b"\x0c\x02\x03" not in transport.writes  # no store
    assert b"\x0c\x02\x07" not in transport.writes  # no flash read
    await client.close()


async def test_gearing_store_reads_header_on_success() -> None:
    transport = FakeTransport()
    client = make_client(transport)
    await client.connect()
    plan = client.gears().set_gears_number(24).set_front_shift(2).plan()
    stored_header = bytes.fromhex("0c0208") + plan.header.pdu[3:]
    transport.responses[b"\x0c\x02\x03"] = [bytes.fromhex("0c0204")]
    transport.responses[b"\x0c\x02\x07"] = [stored_header]

    result = await client.store_gearing()

    assert result.stored is True
    assert result.header_after is not None
    assert result.header_after.raw == stored_header
    assert transport.writes == [b"\x0c\x02\x03", b"\x0c\x02\x07"]
    await client.close()


async def test_gearing_store_failure_skips_header_read() -> None:
    transport = FakeTransport()
    client = make_client(transport)
    await client.connect()
    transport.responses[b"\x0c\x02\x03"] = [bytes.fromhex("0c0206")]

    result = await client.store_gearing()

    assert result.stored is False
    assert result.header_after is None
    assert result.error is not None and "0x06" in result.error
    assert transport.writes == [b"\x0c\x02\x03"]
    await client.close()


class _DelayedStoreTransport(FakeTransport):
    async def write_characteristic(self, uuid: str, data: bytes) -> None:
        if data == b"\x0c\x02\x03":
            self.writes.append(data)
            callback = self.callbacks.get(uuids.normalize(uuids.API_RESPONSE))
            if callback is not None:
                asyncio.get_running_loop().call_later(
                    0.2, callback, bytes.fromhex("0c0206")
                )
            return
        await super().write_characteristic(uuid, data)


async def test_no_api_writes_while_gearing_store_runs() -> None:
    transport = _DelayedStoreTransport(
        {b"\x0e\x00": [bytes.fromhex("0e000000000201")]}
    )
    client = make_client(transport)
    await client.connect()

    store_task = asyncio.create_task(client.store_gearing())
    await asyncio.sleep(0.05)
    poll_task = asyncio.create_task(client.get_bike_status())
    await asyncio.sleep(0.05)
    assert b"\x0e\x00" not in transport.writes

    result = await store_task
    await poll_task
    assert result.stored is False
    assert transport.writes.index(b"\x0c\x02\x03") < transport.writes.index(b"\x0e\x00")
    await client.close()


async def test_gearing_request_failure_raises() -> None:
    transport = FakeTransport()
    client = make_client(transport)
    await client.connect()
    plan = client.gears().set_gears_number(24).set_front_shift(2).plan()
    grade = bytes.fromhex("0f000000000000000000")
    transport.responses[grade] = [grade]
    transport.responses[plan.header.pdu] = [bytes.fromhex("0c0202")]
    with pytest.raises(BikeProtocolError, match="command 2"):
        await client.gears().set_gears_number(24).set_front_shift(2).commit()
    assert transport.writes == [grade, plan.header.pdu]
    await client.close()


async def test_gearing_apply_failure_raises() -> None:
    transport = FakeTransport()
    client = make_client(transport)
    await client.connect()
    plan = client.gears().set_gears_number(24).set_front_shift(2).plan()
    grade = bytes.fromhex("0f000000000000000000")
    transport.responses[grade] = [grade]
    transport.responses[plan.header.pdu] = [bytes.fromhex("0c0201")]
    transport.responses[plan.apply.pdu] = [bytes.fromhex("0c0205")]
    with pytest.raises(BikeProtocolError, match="command 5"):
        await client.gears().set_gears_number(24).set_front_shift(2).commit()
    assert transport.writes[-1] == plan.apply.pdu
    await client.close()


def test_gear_settings_enforce_limits_and_sort() -> None:
    client = SB20Client(transport=FakeTransport())
    with pytest.raises(ValueError):
        client.gears().set_gears_number(9)
    with pytest.raises(ValueError):
        client.gears().set_front_shift(10)
    with pytest.raises(ValueError):
        client.gears().set_front_gears([32, 32])
    with pytest.raises(ValueError):
        client.gears().set_cassette_gears([0, 11])

    plan = (
        client.gears()
        .set_mode(DrivetrainMode.SIMULATED_CASSETTE)
        .set_front_gears([48, 32])
        .set_cassette_gears([10, 11, 12, 13, 14, 15, 17, 19, 21, 24, 28, 33])
        .plan()
    )
    assert plan.front_gears == (32, 48)
    assert plan.cassette_gears == (33, 28, 24, 21, 19, 17, 15, 14, 13, 12, 11, 10)
    assert plan.total_gears == 24
    assert plan.map_size == 14
    assert plan.header.pdu.hex() == "0c020000020c8f00040e00"


async def test_ftms_control_subscribes_and_writes_verbatim() -> None:
    transport = FakeTransport()
    client = SB20Client(
        transport=transport,
        request_timeout=0.2,
        subscribe_buttons=False,
        subscribe_ftms=True,
        subscribe_csc=False,
    )
    await client.connect()
    try:
        assert uuids.FTMS_CONTROL_POINT in transport.callbacks
        await client.ftms_control(bytes.fromhex("0000"))
        assert transport.ftms_writes == [bytes.fromhex("0000")]
        with pytest.raises(pdu.SafetyError):
            await client.ftms_control(b"\x42")
    finally:
        await client.close()


async def test_ftms_control_writes_never_overlap() -> None:
    class SlowTransport(FakeTransport):
        active = 0

        async def write_characteristic(self, uuid: str, data: bytes) -> None:
            if uuid != uuids.FTMS_CONTROL_POINT:
                await super().write_characteristic(uuid, data)
                return
            type(self).active += 1
            assert type(self).active == 1, "two BLE writes were in flight"
            await asyncio.sleep(0.01)
            type(self).active -= 1
            self.ftms_writes.append(data)

    transport = SlowTransport()
    client = SB20Client(
        transport=transport,
        request_timeout=0.2,
        subscribe_buttons=False,
        subscribe_ftms=True,
        subscribe_csc=False,
    )
    await client.connect()
    try:
        await asyncio.gather(*(client.ftms_control(b"\x00\x00") for _ in range(5)))
        assert transport.ftms_writes == [b"\x00\x00"] * 5
    finally:
        await client.close()
