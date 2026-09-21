from AFL.automation.manipulate.OT2GantryDriver import OT2GantryDriver
from AFL.automation.manipulate.OT2Gripper import OT2Gripper

import pytest


class FakeOT2PrepareClient:
    url = "http://ot2-prepare.test:5002"

    def __init__(self):
        self.calls = []
        self.waited = []
        self.fail_next_wait = False
        self.config = {
            "loaded_labware": {
                "1": (
                    "electrode-rack-1",
                    "electrodes",
                    {
                        "definition": {
                            "wells": {
                                "A1": {"z": 20.0, "depth": 10.0},
                                "A2": {"z": 20.0, "depth": 10.0},
                            }
                        }
                    },
                ),
                "2": (
                    "destination-rack-2",
                    "plate",
                    {
                        "definition": {
                            "wells": {"B1": {"z": 25.0, "depth": 15.0}}
                        }
                    },
                ),
            },
            "loaded_instruments": {
                "left": {"pipette_id": "pipette-left"},
                "right": {"pipette_id": "pipette-right"},
            },
            "loaded_gripper_attachments": {
                "right": {
                    "pipette_id": "pipette-right",
                    "gripper_tip_z_offset_mm": 0.0,
                    "horizontal_clearance_mm": 65.0,
                }
            },
        }

    def get_config(self, name, print_console, interactive):
        assert (name, print_console, interactive) == ("all", False, True)
        return {"exit_state": "Success!", "return_val": self.config}

    def enqueue(self, **kwargs):
        if callable(kwargs.get("params")):
            kwargs.update(kwargs.pop("params")())
        self.calls.append(kwargs)
        return f"owner-task-{len(self.calls)}"

    def wait(self, target_uuid, first_check_delay):
        self.waited.append((target_uuid, first_check_delay))
        if self.fail_next_wait:
            self.fail_next_wait = False
            return {"exit_state": "Error!", "return_val": "robot fault"}
        return {"exit_state": "Success!", "return_val": True}


class FakeGripperClient:
    url = "http://10.42.0.231:5058"

    def __init__(self):
        self.calls = []
        self.waited = []
        self.fail_next_wait = False

    def enqueue(self, **kwargs):
        self.calls.append(kwargs)
        return f"gripper-task-{len(self.calls)}"

    def wait(self, target_uuid, first_check_delay):
        self.waited.append((target_uuid, first_check_delay))
        if self.fail_next_wait:
            self.fail_next_wait = False
            return {"exit_state": "Error!", "return_val": "servo fault"}
        return {"exit_state": "Success!", "return_val": True}


@pytest.fixture
def driver(monkeypatch, tmp_path):
    owner = FakeOT2PrepareClient()
    gripper = FakeGripperClient()

    def fake_client(ip, port, username):
        if ip == "ot2-prepare.test":
            assert (port, username) == ("5002", "OT2GantryDriver")
            return owner
        assert (ip, port, username) == ("10.42.0.231", "5058", "OT2Gripper")
        return gripper

    monkeypatch.setattr("AFL.automation.manipulate.OT2GantryDriver.Client", fake_client)
    monkeypatch.setattr("AFL.automation.manipulate.OT2Gripper.Client", fake_client)
    result = OT2Gripper(
        overrides={
            "ot2_prepare_ip": "ot2-prepare.test",
            "ot2_prepare_port": "5002",
            "gripper_mount": "right",
            "translate_z": 105.0,
            "grip_z": 97.5,
            "retract_z": 170.0,
        },
        afl_home=tmp_path,
    )
    return result, owner, gripper


def test_proxy_workflow_waits_for_ot2_and_remote_gripper(driver):
    coordinator, owner, gripper = driver

    assert isinstance(coordinator, OT2GantryDriver)
    assert coordinator.config["gantry_reference_mount"] == "right"
    assert "pickup_electrode" in coordinator.queued.functions
    assert "drop_electrode" in coordinator.queued.functions
    assert "complete_electrode_measurement" in coordinator.queued.functions

    coordinator.register_electrode_racks(["1"])
    picked = coordinator.pickup_electrode("1A2", offset_x=-0.5, offset_y=0.75)
    dropped = coordinator.drop_electrode("1A2", offset_x=2.0, offset_y=1.25)

    assert picked["electrode"]["location"] == "1A2"
    assert picked["measurement_metadata"]["electrode_id"] == "1:A2"
    assert picked["measurement_metadata"]["electrode_use_index"] == 1
    assert picked["offset_x"] == -0.5
    assert picked["offset_y"] == 0.75
    assert dropped["location"] == "1A2"
    assert dropped["offset_x"] == 2.0
    assert dropped["offset_y"] == 1.25
    assert coordinator.config["held_electrode"] is None
    assert coordinator.config["available_electrodes"] == [
        ["electrode-rack-1", "A1"],
        ["electrode-rack-1", "A2"],
    ]
    assert [call["task_name"] for call in gripper.calls] == ["set_angle", "close", "open"]
    assert "angle" in gripper.calls[0]
    assert [call["params"]["wellLocation"]["offset"]["z"] for call in owner.calls] == [
        75.0,
        97.5,
        75.0,
        75.0,
        170.0,
        75.0,
        75.0,
        97.5,
        75.0,
        75.0,
        170.0,
    ]
    assert [call["params"]["wellLocation"]["offset"]["y"] for call in owner.calls] == [
        0.75,
        0.75,
        0.75,
        0.75,
        0.75,
        0.75,
        1.25,
        1.25,
        1.25,
        1.25,
        1.25,
    ]
    assert [call["params"]["wellLocation"]["offset"]["x"] for call in owner.calls] == [
        -0.5,
        -0.5,
        -0.5,
        -0.5,
        -0.5,
        -0.5,
        2.0,
        2.0,
        2.0,
        2.0,
        2.0,
    ]
    assert all(call["params"]["pipetteId"] == "pipette-right" for call in owner.calls)
    assert len(owner.waited) == 11
    assert len(gripper.waited) == 3
    assert all(delay == 0.0 for _, delay in owner.waited + gripper.waited)


def test_pickup_reserves_before_remote_gripper_failure(driver):
    coordinator, _, gripper = driver
    coordinator.register_electrode_racks(["1"])
    gripper.fail_next_wait = True

    with pytest.raises(RuntimeError, match="servo fault"):
        coordinator.pickup_electrode()

    assert coordinator.config["available_electrodes"] == [["electrode-rack-1", "A2"]]
    assert coordinator.config["held_electrode"] is None


def test_drop_returns_only_to_origin_or_discards_to_waste(driver):
    coordinator, _, _ = driver
    coordinator.register_electrode_racks(["1"])
    coordinator.pickup_electrode("1A1")

    assert coordinator.config["occupied_electrode_slots"] == [["electrode-rack-1", "A2"]]
    with pytest.raises(RuntimeError, match="only be returned to its origin slot 1A1"):
        coordinator.drop_electrode("2B1")
    coordinator.drop_electrode()

    assert coordinator.config["occupied_electrode_slots"] == [
        ["electrode-rack-1", "A2"],
        ["electrode-rack-1", "A1"],
    ]
    coordinator.pickup_electrode("1A1", reuse=True)
    coordinator.drop_electrode("2B1", waste=True)

    assert coordinator.config["held_electrode"] is None
    assert coordinator.config["occupied_electrode_slots"] == [["electrode-rack-1", "A2"]]


def test_pickup_requires_reuse_for_a_returned_used_electrode(driver):
    coordinator, _, _ = driver
    coordinator.register_electrode_racks(["1"])
    coordinator.pickup_electrode("1A1")
    coordinator.drop_electrode()

    assert coordinator.config["used_electrode_slots"] == [["electrode-rack-1", "A1"]]
    with pytest.raises(RuntimeError, match="already been used"):
        coordinator.pickup_electrode("1A1")

    picked = coordinator.pickup_electrode("1A1", reuse=True)
    assert picked["electrode"]["location"] == "1A1"


def test_electrode_measurement_count_commits_only_after_completion(driver):
    coordinator, _, _ = driver
    coordinator.register_electrode_racks(["1"])

    picked = coordinator.pickup_electrode("1A1")
    assert picked["measurement_metadata"] == {
        "electrode_id": "1:A1",
        "electrode_origin_location": "1A1",
        "electrode_use_index": 1,
        "electrode_completed_measurement_count": 0,
    }
    assert coordinator.config["electrode_measurement_counts"] == {}

    completed = coordinator.complete_electrode_measurement()
    assert completed["status"] == "completed"
    assert coordinator.config["electrode_measurement_counts"] == {"1:A1": 1}
    assert coordinator.complete_electrode_measurement()["status"] == "already_completed"

    coordinator.drop_electrode()
    reused = coordinator.pickup_electrode("1A1", reuse=True)
    assert reused["measurement_metadata"]["electrode_use_index"] == 2


def test_move_held_electrode_to_loaded_experiment_well(driver):
    coordinator, owner, _ = driver
    coordinator.register_electrode_racks(["1"])
    coordinator.pickup_electrode("1A1")
    owner.calls.clear()
    owner.waited.clear()

    result = coordinator.move_electrode_to_well(
        "2B1", experiment_z=50.0, offset_x=1.5, offset_y=-2.0
    )

    assert result == {
        "status": "at_experiment_well",
        "electrode": coordinator.config["held_electrode"],
        "location": "2B1",
        "translate_z": 105.0,
        "experiment_z": 50.0,
        "offset_x": 1.5,
        "offset_y": -2.0,
    }
    assert [call["params"]["wellLocation"]["offset"] for call in owner.calls] == [
        {"x": 0.0, "y": 0.0, "z": 75.0},
        {"x": 1.5, "y": -2.0, "z": 65.0},
        {"x": 1.5, "y": -2.0, "z": 50.0},
    ]
    assert all(call["params"]["pipetteId"] == "pipette-right" for call in owner.calls)
    assert len(owner.waited) == 3


def test_move_to_experiment_well_requires_a_held_electrode_and_valid_z(driver):
    coordinator, _, _ = driver

    with pytest.raises(RuntimeError, match="No electrode is held"):
        coordinator.move_electrode_to_well("2B1", experiment_z=10)

    coordinator.config["held_electrode"] = {"location": "1A1"}
    with pytest.raises(ValueError, match="experiment_z"):
        coordinator.move_electrode_to_well("2B1", experiment_z=float("nan"))
    with pytest.raises(ValueError, match="offset_x"):
        coordinator.move_electrode_to_well("2B1", experiment_z=10, offset_x=float("nan"))
    with pytest.raises(ValueError, match="offset_y"):
        coordinator.move_electrode_to_well("2B1", experiment_z=10, offset_y=float("nan"))
    coordinator.config["held_electrode"] = None
    with pytest.raises(ValueError, match="offset_x"):
        coordinator.pickup_electrode("1A1", offset_x=float("nan"))
    coordinator.config["held_electrode"] = {"location": "1A1"}
    with pytest.raises(ValueError, match="offset_y"):
        coordinator.drop_electrode("2B1", offset_y=float("nan"))


def test_gripper_rejects_moves_below_labware_clearance(driver):
    coordinator, owner, _ = driver
    coordinator.config["held_electrode"] = {"location": "1A1"}
    owner.config["loaded_gripper_attachments"]["right"]["horizontal_clearance_mm"] = 65.1

    with pytest.raises(ValueError, match="translate_z"):
        coordinator.move_electrode_to_well("2B1", experiment_z=10.0)
    with pytest.raises(RuntimeError, match="direct gantry moves are disabled"):
        coordinator.move_to_well("2B1", offset_z=64.9)
    with pytest.raises(RuntimeError, match="direct gantry moves are disabled"):
        coordinator.move_pipette(dz=100.0)

    # The translation height is rejected before a move is submitted.
    # before it is submitted to OT2Prepare.
    assert owner.calls == []


def test_gripper_retracts_before_xy_travel_and_descends_afterward(driver):
    coordinator, owner, _ = driver
    owner_config = coordinator._owner_config()
    source = coordinator._resolve_location(owner_config, "1A1")
    target = coordinator._resolve_location(owner_config, "2B1")
    coordinator._gripper_motion_state = {
        "target": source,
        "offset": {"x": 0.0, "y": 0.0, "z": 97.5},
    }

    coordinator._move_and_wait(owner_config, target, 97.5)

    assert [call["params"]["wellName"] for call in owner.calls] == ["A1", "B1", "B1"]
    assert [call["params"]["wellLocation"]["offset"]["z"] for call in owner.calls] == [
        75.0,
        65.0,
        97.5,
    ]


def test_gripper_profile_offset_is_applied_to_pipette_motion(driver):
    coordinator, owner, _ = driver
    owner.config["loaded_gripper_attachments"]["right"]["gripper_tip_z_offset_mm"] = -42.5
    owner_config = coordinator._owner_config()
    target = coordinator._resolve_location(owner_config, "2B1")

    coordinator._move_and_wait(owner_config, target, 105.0)

    assert [call["params"]["wellLocation"]["offset"]["z"] for call in owner.calls] == [
        107.5,
        147.5,
    ]


def test_translate_z_stays_at_one_deck_height_for_unequal_labware(driver):
    coordinator, owner, _ = driver
    owner_config = coordinator._owner_config()
    source = coordinator._resolve_location(owner_config, "1A1")
    target = coordinator._resolve_location(owner_config, "2B1")

    coordinator._move_and_wait(owner_config, source, 20.0)
    coordinator._move_and_wait(owner_config, target, 20.0)

    translate_calls = [
        call for call in owner.calls
        if call["params"]["wellLocation"]["offset"]["z"] in {75.0, 65.0}
    ]
    assert [
        call["params"]["wellLocation"]["offset"]["z"]
        + (30.0 if call["params"]["wellName"] == "A1" else 40.0)
        for call in translate_calls
    ] == [105.0, 105.0, 105.0]


def test_translate_rejects_loaded_labware_without_well_geometry(driver):
    coordinator, owner, _ = driver
    coordinator.config["held_electrode"] = {"location": "1A1"}
    owner.config["loaded_labware"]["1"][2]["definition"]["wells"]["A2"] = {}

    with pytest.raises(ValueError, match="no usable z and depth geometry"):
        coordinator.move_electrode_to_well("2B1", experiment_z=20.0)

    assert owner.calls == []


def test_translate_rejects_labware_on_a_module_without_deck_height(driver):
    coordinator, owner, _ = driver
    coordinator.config["held_electrode"] = {"location": "1A1"}
    owner.config["loaded_modules"] = {"1": ("module-1", "temperatureModule")}

    with pytest.raises(ValueError, match="module slot"):
        coordinator.move_electrode_to_well("2B1", experiment_z=20.0)

    assert owner.calls == []


def test_registration_and_motion_validation(driver):
    coordinator, _, _ = driver

    with pytest.raises(ValueError, match="non-empty"):
        coordinator.register_electrode_racks([])
    with pytest.raises(RuntimeError, match="No electrode racks"):
        coordinator.reset_electrode_racks()

    coordinator.config["grip_z"] = None
    coordinator.register_electrode_racks(["1"])
    with pytest.raises(ValueError, match="grip_z"):
        coordinator.pickup_electrode("1A1")
    with pytest.raises(RuntimeError, match="No electrode is held"):
        coordinator.drop_electrode("2B1")
