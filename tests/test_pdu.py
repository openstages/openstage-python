"""Codec tests using representative SB20 frames."""

from __future__ import annotations

import pytest

from openstage import pdu
from openstage.pdu import (
    AlreadyConnected,
    Battery,
    BikeStatus,
    BrakeFrame,
    ButtonAssignments,
    ButtonFrame,
    CscMeasurement,
    ErrorPdu,
    FtmsIndoorBikeData,
    GearMapFragment,
    GearModelHeader,
    GearNotification,
    Parameters,
    PowerData,
    PowerMeterPairing,
    SctsIdentity,
    SensorFirmware,
    SensorStatus,
    Stream,
    Weight,
    decode_notification,
    decode_pdu,
    ensure_read_only,
)


def test_bike_status_hardware_frame() -> None:
    record = decode_pdu(bytes.fromhex("0e000000000201"))
    assert isinstance(record, BikeStatus)
    assert record.flywheel_rpm == 0.0
    assert record.hrm is SensorStatus.DISCONNECTED
    assert record.power_meter is SensorStatus.CONNECTED
    assert record.pairing is pdu.PairingStatus.PAIRED


def test_flywheel_quarter_rpm() -> None:
    record = decode_pdu(bytes.fromhex("0e006819000201"))
    assert isinstance(record, BikeStatus)
    assert record.flywheel_rpm == 1626.0


def test_pairing_hardware_frame() -> None:
    record = decode_pdu(bytes.fromhex("0a014a0180a91e850a03aeb2c35b6d02"))
    assert isinstance(record, PowerMeterPairing)
    assert record.command == 1
    assert record.crank_length_mm == 165.0
    assert record.left_ant_id == 43392
    assert record.left_serial == 51021086
    assert record.right_ant_id == 45742
    assert record.right_serial == 40721347


def test_parameters_hardware_frame() -> None:
    record = decode_pdu(bytes.fromhex("10010102"))
    assert isinstance(record, Parameters)
    assert record.brake_enabled is True
    assert record.dual_ble_enabled is True


def test_weight_hardware_frame() -> None:
    record = decode_pdu(bytes.fromhex("0300d0204006"))
    assert isinstance(record, Weight)
    assert record.user_kg == 84.0
    assert record.equipment_kg == 16.0


def test_gradient_scaling_hardware_frame() -> None:
    record = decode_pdu(bytes.fromhex("0500c80000"))
    assert isinstance(record, pdu.GradientScaling)
    assert record.factor == 200
    assert record.percent == 100.0
    assert record.delay_enabled is False


def test_resistance_hardware_frame() -> None:
    record = decode_pdu(bytes.fromhex("040000"))
    assert isinstance(record, pdu.Resistance)
    assert record.level == 0
    assert record.percent == 0.0


def test_battery_unknown_and_known() -> None:
    unknown = decode_pdu(bytes.fromhex("0700ff"))
    assert isinstance(unknown, Battery)
    assert unknown.percent is None
    known = decode_pdu(bytes.fromhex("070050"))
    assert isinstance(known, Battery)
    assert known.percent == 80


def test_gear_get_response() -> None:
    record = decode_pdu(bytes.fromhex("0c0100010322001c000004"))
    assert isinstance(record, GearNotification)
    assert record.model is pdu.ModelType.FRONT_REAR_U8
    assert (record.front_index, record.rear_index) == (1, 3)
    assert (record.front_teeth, record.rear_teeth) == (34, 28)
    assert record.dream_drive is False
    assert record.dream_drive_step == 4


def test_model_header_and_success() -> None:
    for hex_frame in ("0c020800010c0f00040d00", "0c020900010c0f00040d00"):
        record = decode_pdu(bytes.fromhex(hex_frame))
        assert isinstance(record, GearModelHeader)
        assert record.command in (
            pdu.GearingTransferCommand.GET_CURRENT_MODEL_HEADER,
            pdu.GearingTransferCommand.GET_CURRENT_MODEL_SUCCESS,
        )
        assert record.model is pdu.ModelType.FRONT_REAR_U8
        assert (record.front_count, record.rear_count) == (1, 12)
        assert record.crc8 == 0x0F
        assert record.map_size == 13
        assert record.dream_drive_step == 4
        assert record.dream_drive is False


def test_map_fragment() -> None:
    record = decode_pdu(bytes.fromhex("0c032224201c18151311"))
    assert isinstance(record, GearMapFragment)
    assert record.data.hex() == "2224201c18151311"


def test_button_assignments() -> None:
    record = decode_pdu(bytes.fromhex("0b01030405030405030404030102030400"))
    assert isinstance(record, ButtonAssignments)
    assert record.command == 1
    assert record.values == (3, 4, 5, 3, 4, 5, 3, 4, 4, 3, 1, 2, 3, 4, 0)
    assert record.by_slot[1] == 3
    assert record.by_slot[19] == 0
    for slot in (4, 5, 9, 10):
        assert slot not in record.by_slot


def test_power_data_event() -> None:
    # 0x08/2, mask 0x1F, instant 80 W, accumulated 80 W, cadence 77, dist 36, diff 1
    record = decode_pdu(bytes.fromhex("08021f50000000500000004d002401"))
    assert isinstance(record, PowerData)
    assert record.mask == 0x1F
    assert record.instantaneous_w == 80
    assert record.accumulated_w == 80
    assert record.cadence_rpm == 77
    assert record.distribution_pct == 36
    assert record.lr_difference == 1


def test_power_data_ack() -> None:
    record = decode_pdu(bytes.fromhex("0800"))
    assert isinstance(record, pdu.PowerDataAck)
    assert record.command is pdu.PowerDataCommand.SUBSCRIBE


def test_gearing_subscription_ack() -> None:
    record = decode_pdu(bytes.fromhex("0c0001"))
    assert isinstance(record, pdu.GearSubscriptionAck)
    assert record.command == 1


def test_scts_identity() -> None:
    record = decode_pdu(bytes.fromhex("0001000000"))
    assert isinstance(record, SctsIdentity)
    assert record.identifier == 1
    assert record.version == 0


def test_sensor_firmware_binary_and_verbose() -> None:
    binary = decode_pdu(bytes.fromhex("0d0a01020304050607"))
    assert isinstance(binary, SensorFirmware)
    assert binary.version == "1.2.3"
    assert binary.major == 1
    assert binary.manufacturer == 0x0504
    assert binary.ant_id == 0x0706

    verbose = decode_pdu(bytes.fromhex("0d0a341276312e322e33"))
    assert isinstance(verbose, SensorFirmware)
    assert verbose.ant_id == 0x1234
    assert verbose.version == "v1.2.3"

    # Firmware response: request command echoed, ANT id + UTF-8 version.
    echo = decode_pdu(bytes.fromhex("0d0280a9312e382e32"))
    assert isinstance(echo, SensorFirmware)
    assert echo.command == pdu.FirmwareCommand.GET_LEFT
    assert echo.ant_id == 43392
    assert echo.version == "1.8.2"


def test_error_and_control_pdus() -> None:
    error = decode_pdu(bytes.fromhex("fe0e00"))
    assert isinstance(error, ErrorPdu)
    assert error.failed_op == 0x0E
    assert error.code == 0
    already = decode_pdu(b"\xfbStages Bike")
    assert isinstance(already, AlreadyConnected)
    assert already.controller_address == "Stages Bike"


def test_ftms_indoor_bike_data_shapes() -> None:
    speed = decode_notification(Stream.FTMS_DATA, bytes.fromhex("0000e803"))
    assert isinstance(speed, FtmsIndoorBikeData)
    assert speed.speed_kph == 10.0

    distance = decode_notification(Stream.FTMS_DATA, bytes.fromhex("1100a08601"))
    assert isinstance(distance, FtmsIndoorBikeData)
    assert distance.distance_m == 100000

    power = decode_notification(Stream.FTMS_DATA, bytes.fromhex("c5004e0064005a00"))
    assert isinstance(power, FtmsIndoorBikeData)
    assert power.cadence_rpm == 39.0
    assert power.power_w == 100
    assert power.average_power_w == 90


def test_csc_measurement() -> None:
    record = decode_notification(Stream.CSC, bytes.fromhex("0234120001"))
    assert isinstance(record, CscMeasurement)
    assert record.crank_revs == 0x1234
    assert record.crank_event_time_s == 256 / 1024


def test_button_and_brake_frames() -> None:
    button = decode_notification(Stream.BUTTONS, bytes.fromhex("01000400"))
    assert isinstance(button, ButtonFrame)
    assert button.selector == 1
    assert button.mask == 0x0004

    pair = decode_notification(Stream.BUTTONS, bytes.fromhex("090004002000"))
    assert isinstance(pair, ButtonFrame)
    assert pair.values == (0x0004, 0x0020)

    brake = decode_notification(Stream.BRAKE, bytes.fromhex("e8036400"))
    assert isinstance(brake, BrakeFrame)
    assert (brake.left, brake.right) == (1000, 100)


def test_unknown_pdu_is_retained() -> None:
    record = decode_pdu(bytes.fromhex("11"))
    assert isinstance(record, pdu.UnknownPdu)
    assert record.raw == b"\x11"


def test_read_only_allow_list_accepts_queries_and_subscriptions() -> None:
    for request in pdu.READ_ONLY_REQUESTS:
        assert ensure_read_only(request)
    assert ensure_read_only(b"\x0e\x00") == pdu.BIKE_STATUS.label
    assert ensure_read_only(bytes.fromhex("0800")) == pdu.SUBSCRIBE_POWER_DATA.label


@pytest.mark.parametrize(
    "hex_frame",
    [
        "0200",      # set target power
        "0401",      # set resistance
        "0f00",      # set grade
        "0301",      # set weight
        "0600",      # zero reset
        "0100",      # assign sensor
        "0b00",      # set buttons
        "0c0200",    # request drivetrain transfer
        "0c0203",    # store drivetrain
        "0c020b",    # apply drivetrain
        "0c03",      # drivetrain map fragment
        "0a03",      # restart pairing
        "1000",      # set parameters
        "fd00",      # store settings
        "fc00",      # factory reset
        "fc05",      # set UICR
    ],
)
def test_configuration_writes_are_refused(hex_frame: str) -> None:
    with pytest.raises(pdu.SafetyError):
        ensure_read_only(bytes.fromhex(hex_frame))


def test_empty_write_is_refused() -> None:
    with pytest.raises(pdu.SafetyError):
        ensure_read_only(b"")


def test_write_encoders_golden_pdus() -> None:
    assert pdu.build_set_weight(85.0, 7.5).pdu == bytes.fromhex("03013421ee02")
    assert pdu.build_set_parameters(True, None).pdu == bytes.fromhex("10000100")
    assert pdu.build_set_parameters(False, True).pdu == bytes.fromhex("10000002")
    values = [1, 2, 5, 3, 4, 5, 1, 2, 4, 3, 1, 2, 3, 4, 0]
    assert pdu.build_set_buttons(values).pdu == bytes.fromhex(
        "0b00" + "".join(f"{value:02x}" for value in values)
    )
    assert pdu.STORE_SETTINGS_WRITE.pdu == b"\xfd\x00"
    assert pdu.build_set_button_slots(values[:14]).pdu == pdu.build_set_buttons(
        (*values[:14], 0)
    ).pdu
    with pytest.raises(ValueError):
        pdu.build_set_button_slots(values)


def test_write_allow_list_accepts_only_approved_slice() -> None:
    for request in (
        pdu.build_set_weight(85.0, 7.5),
        pdu.build_set_parameters(True, False),
        pdu.build_set_buttons([0] * 15),
        pdu.STORE_SETTINGS_WRITE,
    ):
        assert pdu.ensure_write_allowed(request.pdu)
        with pytest.raises(pdu.SafetyError):
            ensure_read_only(request.pdu)

    for forbidden in (
        bytes.fromhex("0200"),          # ERG target power
        bytes.fromhex("0401"),          # set resistance
        bytes.fromhex("0c0200"),        # gearing transfer
        bytes.fromhex("fd01"),          # store in-progress is not a request
        bytes.fromhex("fc00"),          # factory control
        bytes.fromhex("0301"),          # truncated set-weight
        bytes.fromhex("10000300"),      # invalid brake tri-state
        bytes.fromhex("0b00" + "00" * 14),  # truncated buttons
        bytes.fromhex("0b00" + "2c" + "00" * 14),  # button value 44
    ):
        with pytest.raises(pdu.SafetyError):
            pdu.ensure_write_allowed(forbidden)


def test_semantic_button_actions() -> None:
    assert pdu.parse_button_action("shift rear up") is pdu.ButtonAction.REAR_UP
    assert pdu.parse_button_action("cassette-") is pdu.ButtonAction.REAR_DOWN
    assert pdu.parse_button_action("front+") is pdu.ButtonAction.FRONT_UP
    assert pdu.parse_button_action("external") is pdu.ButtonAction.NOT_ASSIGNED
    with pytest.raises(ValueError):
        pdu.parse_button_action("nope")

    actions = (
        pdu.ButtonAction.FRONT_UP,
        pdu.ButtonAction.FRONT_DOWN,
        pdu.ButtonAction.NOT_ASSIGNED,
        pdu.ButtonAction.REAR_UP,
        pdu.ButtonAction.REAR_DOWN,
        pdu.ButtonAction.NOT_ASSIGNED,
        pdu.ButtonAction.FRONT_UP,
        pdu.ButtonAction.FRONT_DOWN,
        pdu.ButtonAction.REAR_DOWN,
        pdu.ButtonAction.REAR_UP,
        pdu.ButtonAction.NOT_ASSIGNED,
        pdu.ButtonAction.NOT_ASSIGNED,
        pdu.ButtonAction.NOT_ASSIGNED,
        pdu.ButtonAction.NOT_ASSIGNED,
    )
    request = pdu.build_set_button_actions(actions)
    assert request.pdu[2:16] == bytes(
        (1, 2, 5, 3, 4, 5, 1, 2, 4, 3, 5, 5, 5, 5)
    )
    assert request.pdu[16] == 0
    assert pdu.button_actions_from_wire(request.pdu[2:]) == actions

    current = (1, 2, 5, 3, 4, 5, 1, 2, 4, 3, 1, 2, 3, 4, 0)
    physical = pdu.apply_physical_button_actions(
        current, {1: pdu.ButtonAction.REAR_UP, 2: pdu.ButtonAction.REAR_DOWN}
    )
    assert physical.pdu[2:16] == bytes((3, 4, 5, 3, 4, 5, 1, 2, 4, 3, 1, 2, 3, 4))
    assert physical.pdu[16] == 0
    physical = pdu.apply_physical_button_actions(
        current, {4: pdu.ButtonAction.REAR_DOWN}
    )
    assert physical.pdu[2:16] == bytes((1, 2, 5, 4, 4, 5, 1, 2, 4, 3, 1, 2, 3, 4))
    with pytest.raises(ValueError):
        pdu.apply_physical_button_actions(current, {15: pdu.ButtonAction.REAR_UP})


def test_store_settings_decode() -> None:
    in_progress = pdu.decode_pdu(bytes.fromhex("fd01"))
    stored = pdu.decode_pdu(bytes.fromhex("fd03"))
    applied = pdu.decode_pdu(bytes.fromhex("fd04"))
    busy = pdu.decode_pdu(bytes.fromhex("fd02"))
    assert isinstance(in_progress, pdu.StoreSettings)
    assert isinstance(stored, pdu.StoreSettings)
    assert isinstance(applied, pdu.StoreSettings)
    assert isinstance(busy, pdu.StoreSettings)
    assert in_progress.status is pdu.StoreSettingsCommand.IN_PROGRESS
    assert stored.stored is True
    assert applied.stored is True
    assert busy.status is pdu.StoreSettingsCommand.ERROR
    assert busy.stored is False


def test_control_and_config_encoders() -> None:
    assert pdu.build_set_gradient_scaling(200, False).pdu == bytes.fromhex("0501c80000")
    assert pdu.build_set_gradient_scaling_percent(100.0, True).pdu == bytes.fromhex("0501c80001")
    assert pdu.build_set_resistance(128).pdu == bytes.fromhex("040180")
    assert pdu.build_set_resistance_percent(50.0).pdu == bytes.fromhex("040180")
    assert pdu.build_set_crank_length(172.5).pdu == bytes.fromhex("0a035901")
    assert pdu.build_set_target_power(200).pdu == bytes.fromhex("02009001000000000000")
    assert pdu.build_set_grade(5.0).pdu == bytes.fromhex("0f008813000000000000")
    assert pdu.ZERO_RESET_WRITE.pdu == bytes.fromhex("0600")

    for forbidden in (164.9, 167.5, 180.0):
        with pytest.raises(ValueError):
            pdu.build_set_crank_length(forbidden)
    with pytest.raises(ValueError):
        pdu.build_set_target_power(1001)
    with pytest.raises(ValueError):
        pdu.build_set_grade(20.1)
    with pytest.raises(ValueError):
        pdu.build_set_resistance(256)
    with pytest.raises(ValueError):
        pdu.build_set_gradient_scaling(70000, False)


def test_control_write_allow_list() -> None:
    for request in (
        pdu.build_set_gradient_scaling(200, False),
        pdu.build_set_resistance(128),
        pdu.build_set_crank_length(175.0),
        pdu.build_set_target_power(200),
        pdu.build_set_grade(-5.5),
        pdu.ZERO_RESET_WRITE,
    ):
        assert pdu.ensure_write_allowed(request.pdu)

    for forbidden in (
        bytes.fromhex("0200a00f010000000000"),  # target 1000.5 W? raw 2001
        bytes.fromhex("0f00214e000000000000"),  # grade 20.001 %
        bytes.fromhex("040101"),                # valid resistance, should pass
        bytes.fromhex("0a035a01"),              # crank 173 mm
        bytes.fromhex("0501c802"),              # delay 2
        bytes.fromhex("060000"),                # invalid zero reset
    ):
        if forbidden == bytes.fromhex("040101"):
            assert pdu.ensure_write_allowed(forbidden)
        else:
            with pytest.raises(pdu.SafetyError):
                pdu.ensure_write_allowed(forbidden)


def test_zero_reset_decode() -> None:
    frame = bytes.fromhex("0601011ff6ff1400785634123412fbff")
    record = pdu.decode_pdu(frame)
    assert isinstance(record, pdu.ZeroReset)
    assert record.command == 1
    assert record.status is pdu.ZeroResetStatus.SUCCESS
    assert record.successful is True
    assert record.valid_mask == 0x1F
    assert record.left_offset == -10
    assert record.right_offset == 20
    assert record.manufacturer_code == 0x12345678
    assert record.company_id == 0x1234
    assert record.torque_offset == -5


def test_zero_reset_short_error_frames() -> None:
    no_connection = pdu.decode_pdu(bytes.fromhex("060102"))
    assert isinstance(no_connection, pdu.ZeroReset)
    assert no_connection.status is pdu.ZeroResetStatus.ERROR_NO_CONNECTION
    assert no_connection.valid_mask == 0
    assert no_connection.left_offset is None
    assert no_connection.right_offset is None

    invalid_position = pdu.decode_pdu(bytes.fromhex("060105"))
    assert isinstance(invalid_position, pdu.ZeroReset)
    assert invalid_position.status is pdu.ZeroResetStatus.ERROR_INVALID_CRANK_POSITION
    assert invalid_position.valid_mask == 0


def test_gearing_crc8_vectors() -> None:
    assert pdu.crc8(bytes.fromhex("2224201c18151311")) == 0xE1
    assert pdu.crc8(bytes.fromhex("2224201c181513110f0d0b0907")) == 0x79


def test_dream_drive_map_uses_float32_and_matches_live_header() -> None:
    coefficients = pdu.dream_drive_coefficients(24)
    assert coefficients[:4] == (500, 782, 1065, 1347)
    assert coefficients[-1] == 6999
    assert pdu.build_dream_drive_map(24).hex() == (
        "f4010e03290443055e0679079308ae09c80ae30bfe0c180e330f"
        "4d10681183129d13b814d215ed16081822193d1a571b"
    )
    assert pdu.crc8(pdu.build_dream_drive_map(24)) == 0x47
    assert pdu.dream_drive_coefficients(25)[:4] == (500, 770, 1041, 1312)
    assert pdu.crc8(pdu.build_dream_drive_map(25)) == 0x2F


def test_model0_map_sorts_and_matches_shimano_vector() -> None:
    map_bytes = pdu.build_front_rear_map(
        [52, 36],
        [11, 13, 15, 17, 19, 21, 23, 25, 27, 30, 34],
    )
    assert map_bytes.hex() == "2434221e1b19171513110f0d0b"
    assert pdu.crc8(map_bytes) == 0xC5


def test_gearing_header_fragment_and_apply_builders() -> None:
    dream = pdu.build_gearing_header(
        model=pdu.ModelType.COEFFICIENTS,
        front_count=1,
        rear_count=24,
        crc8=0x47,
        dream_drive=True,
        step=2,
        map_size=48,
    )
    assert dream.pdu.hex() == "0c02000201184701023000"
    assert pdu.ensure_write_allowed(dream.pdu)

    model0 = pdu.build_gearing_header(
        model=pdu.ModelType.FRONT_REAR_U8,
        front_count=2,
        rear_count=11,
        crc8=0xC5,
        dream_drive=False,
        step=0,
        map_size=13,
    )
    assert model0.pdu.hex() == "0c020000020bc500000d00"
    assert pdu.ensure_write_allowed(model0.pdu)

    fragments = pdu.split_gearing_map(bytes(range(40)))
    assert [len(fragment) for fragment in fragments] == [18, 18, 4]
    for chunk in fragments:
        request = pdu.build_gearing_fragment(chunk)
        assert request.pdu[:2] == b"\x0c\x03"
        assert pdu.ensure_write_allowed(request.pdu)
    apply = pdu.build_gearing_apply(dream.pdu)
    assert apply.pdu.hex() == "0c020b0201184701023000"
    assert pdu.ensure_write_allowed(apply.pdu)
    with pytest.raises(ValueError):
        pdu.build_gearing_apply(b"\x0c\x02\x0b")


def test_gearing_limits_and_allow_list_reject_unsafe_values() -> None:
    with pytest.raises(ValueError):
        pdu.dream_drive_coefficients(pdu.DREAM_DRIVE_GEARS_MIN - 1)
    with pytest.raises(ValueError):
        pdu.dream_drive_coefficients(pdu.DREAM_DRIVE_GEARS_MAX + 1)
    with pytest.raises(ValueError):
        pdu.build_gearing_header(
            model=pdu.ModelType.COEFFICIENTS,
            front_count=1,
            rear_count=24,
            crc8=0,
            dream_drive=True,
            step=pdu.DREAM_DRIVE_STEP_MAX + 1,
            map_size=48,
        )
    with pytest.raises(ValueError):
        pdu.sorted_front_gears([32, 32])
    with pytest.raises(ValueError):
        pdu.sorted_cassette_gears([0, 11])
    with pytest.raises(ValueError):
        pdu.build_gearing_fragment(b"x" * (pdu.GEARING_FRAGMENT_MAX + 1))

    for forbidden in (
        bytes.fromhex("0c02000101010000000000"),  # model 1
        bytes.fromhex("0c02000201184701022f00"),  # step 47
        bytes.fromhex("0c020000020cc500000d00"),  # wrong model 0 size
        bytes.fromhex("0c020c"),                  # response used as request
        bytes.fromhex("0c02"),                    # truncated transfer
        bytes.fromhex("0c0300" * 8),              # over-long fragment
    ):
        with pytest.raises(pdu.SafetyError):
            pdu.ensure_write_allowed(forbidden)
