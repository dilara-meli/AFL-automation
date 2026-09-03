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
                    {"definition": {"wells": {"A1": {}, "A2": {}}}},
                ),
                "2": (
                    "destination-rack-2",
                    "plate",
                    {"definition": {"wells": {"B1": {}}}},
                ),
            },
            "loaded_instruments": {
                "left": {"pipette_id": "pipette-left"},
                "right": {"pipette_id": "pipette-right"},
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
            "approach_z": 10.0,
            "grip_z": -2.0,
            "retract_z": 15.0,
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

    coordinator.register_electrode_racks(["1"])
    picked = coordinator.pickup_electrode("1A2")
    dropped = coordinator.drop_electrode("2B1", offset_y=1.25)

    assert picked["electrode"]["location"] == "1A2"
    assert dropped["location"] == "2B1"
    assert dropped["offset_y"] == 1.25
    assert coordinator.config["held_electrode"] is None
    assert coordinator.config["available_electrodes"] == [["electrode-rack-1", "A1"]]
    assert [call["task_name"] for call in gripper.calls] == ["set_angle", "close", "open"]
    assert gripper.calls[0]["angle"] == 60
    assert [call["params"]["wellLocation"]["offset"]["z"] for call in owner.calls] == [
        10.0,
        -2.0,
        15.0,
        10.0,
        -2.0,
        15.0,
    ]
    assert [call["params"]["wellLocation"]["offset"]["y"] for call in owner.calls] == [
        0.0,
        0.0,
        0.0,
        1.25,
        1.25,
        1.25,
    ]
    assert all(call["params"]["pipetteId"] == "pipette-right" for call in owner.calls)
    assert len(owner.waited) == 6
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


def test_move_held_electrode_to_loaded_experiment_well(driver):
    coordinator, owner, _ = driver
    coordinator.register_electrode_racks(["1"])
    coordinator.pickup_electrode("1A1")
    owner.calls.clear()
    owner.waited.clear()

    result = coordinator.move_electrode_to_well("2B1", experiment_z=12.5)

    assert result == {
        "status": "at_experiment_well",
        "electrode": coordinator.config["held_electrode"],
        "location": "2B1",
        "approach_z": 170.0,
        "experiment_z": 12.5,
    }
    assert [call["params"]["wellLocation"]["offset"] for call in owner.calls] == [
        {"x": 0.0, "y": 0.0, "z": 170.0},
        {"x": 0.0, "y": 0.0, "z": 12.5},
    ]
    assert all(call["params"]["pipetteId"] == "pipette-right" for call in owner.calls)
    assert len(owner.waited) == 2


def test_move_to_experiment_well_requires_a_held_electrode_and_valid_z(driver):
    coordinator, _, _ = driver

    with pytest.raises(RuntimeError, match="No electrode is held"):
        coordinator.move_electrode_to_well("2B1", experiment_z=10)

    coordinator.config["held_electrode"] = {"location": "1A1"}
    with pytest.raises(ValueError, match="experiment_z"):
        coordinator.move_electrode_to_well("2B1", experiment_z=float("nan"))
    with pytest.raises(ValueError, match="offset_y"):
        coordinator.drop_electrode("2B1", offset_y=float("nan"))


def test_registration_and_motion_validation(driver):
    coordinator, _, _ = driver

    with pytest.raises(ValueError, match="non-empty"):
        coordinator.register_electrode_racks([])
    with pytest.raises(RuntimeError, match="No electrode racks"):
        coordinator.reset_electrode_racks()

    coordinator.config["approach_z"] = None
    coordinator.register_electrode_racks(["1"])
    with pytest.raises(ValueError, match="approach_z"):
        coordinator.pickup_electrode("1A1")
    with pytest.raises(RuntimeError, match="No electrode is held"):
        coordinator.drop_electrode("2B1")
