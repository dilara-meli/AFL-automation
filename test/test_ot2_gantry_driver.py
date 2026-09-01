import pytest

from AFL.automation.manipulate.OT2GantryDriver import OT2GantryDriver


class FakeOT2PrepareClient:
    url = "http://ot2-prepare.test:5002"

    def __init__(self):
        self.calls = []
        self.config = {
            "loaded_labware": {
                "1": (
                    "labware-1",
                    "plate",
                    {
                        "definition": {
                            "wells": {"A1": {}, "A2": {}},
                            "dimensions": {"z": 10},
                        }
                    },
                )
            },
            "loaded_instruments": {"left": {"pipette_id": "pipette-left"}},
        }

    def get_config(self, name, print_console, interactive):
        self.calls.append(("get_config", name, print_console, interactive))
        return {"exit_state": "Success!", "return_val": self.config}

    def enqueue(self, **kwargs):
        if "params" in kwargs and callable(kwargs["params"]):
            kwargs.update(kwargs.pop("params")())
        self.calls.append(("enqueue", kwargs))
        return f"owner-task-{sum(call[0] == 'enqueue' for call in self.calls)}"


def make_driver(monkeypatch):
    client = FakeOT2PrepareClient()
    seen = []

    def fake_client(ip, port, username):
        seen.append((ip, port, username))
        return client

    monkeypatch.setattr("AFL.automation.manipulate.OT2GantryDriver.Client", fake_client)
    driver = OT2GantryDriver(overrides={"ot2_prepare_ip": "ot2-prepare.test", "ot2_prepare_port": "5002"})
    return driver, client, seen


def test_move_to_well_builds_client_from_ip_and_port(monkeypatch):
    driver, client, seen = make_driver(monkeypatch)

    result = driver.move_to_well("1a2", offset_x=1.5, offset_z=2)

    assert seen == [("ot2-prepare.test", "5002", "OT2GantryDriver")]
    assert result == {
        "owner_task_uuids": ["owner-task-1"],
        "owner_server": "http://ot2-prepare.test:5002",
    }
    assert client.calls == [
        ("get_config", "all", False, True),
        (
            "enqueue",
            {
                "task_name": "_execute_atomic_command",
                "command_type": "moveToWell",
                "params": {
                    "pipetteId": "pipette-left",
                    "labwareId": "labware-1",
                    "wellName": "A2",
                    "wellLocation": {
                        "origin": "top",
                        "offset": {"x": 1.5, "y": 0.0, "z": 2.0},
                    },
                },
                "interactive": False,
            },
        ),
    ]


def test_constructor_target_settings_override_config(monkeypatch):
    client = FakeOT2PrepareClient()
    seen = []

    def fake_client(ip, port, username):
        seen.append((ip, port, username))
        return client

    monkeypatch.setattr(
        "AFL.automation.manipulate.OT2GantryDriver.Client",
        fake_client,
    )

    driver = OT2GantryDriver(
        overrides={"ot2_prepare_ip": "ignored.test", "ot2_prepare_port": "5001"},
        ot2_prepare_ip="ot2-prepare.test",
        ot2_prepare_port=5002,
    )

    assert driver.config["ot2_prepare_ip"] == "ot2-prepare.test"
    assert driver.config["ot2_prepare_port"] == "5002"
    driver._get_ot2_prepare_client()
    assert seen == [("ot2-prepare.test", "5002", "OT2GantryDriver")]


def test_move_to_well_uses_configured_offsets_and_allows_per_axis_overrides(monkeypatch):
    driver, client, _ = make_driver(monkeypatch)
    driver.config["offset_x"] = 1.5
    driver.config["offset_y"] = -2.5
    driver.config["offset_z"] = 3.5

    driver.move_to_well("1A1", offset_y=4.0)

    final_move = client.calls[-1][1]["params"]
    assert final_move["wellLocation"] == {
        "origin": "top",
        "offset": {"x": 1.5, "y": 4.0, "z": 3.5},
    }


def test_move_to_well_uses_only_the_configured_reference_mount(monkeypatch):
    driver, client, _ = make_driver(monkeypatch)
    client.config["loaded_instruments"]["right"] = {"pipette_id": "pipette-right"}
    driver.config["gantry_reference_mount"] = "right"

    driver.move_to_well("1A1")

    assert client.calls[-1][1]["params"]["pipetteId"] == "pipette-right"
    assert "move_pipette_up" not in driver.queued.functions
    assert "move_pipette_down" not in driver.queued.functions


def test_move_to_well_applies_the_reference_pipette_parked_height(monkeypatch):
    driver, client, _ = make_driver(monkeypatch)
    driver.config["left_pipette_zero"] = 42.0
    driver.config["offset_z"] = 1.5

    driver.move_to_well("1A1")

    assert client.calls[-1][1]["params"]["wellLocation"]["offset"] == {
        "x": 0.0,
        "y": 0.0,
        "z": 43.5,
    }


def test_move_pipette_queues_and_accumulates_well_relative_offsets(monkeypatch):
    driver, client, _ = make_driver(monkeypatch)
    driver.move_to_well("1A1")
    client.calls.clear()

    result = driver.move_pipette(dx=1, dy=-2, dz=3)
    driver.move_pipette("left", dx=4, dy=5, dz=-6)

    assert result["owner_task_uuids"] == ["owner-task-1"]
    assert client.calls[1][1]["params"] == {
        "pipetteId": "pipette-left",
        "labwareId": "labware-1",
        "wellName": "A1",
        "wellLocation": {
            "origin": "top",
            "offset": {"x": 1.0, "y": -2.0, "z": 3.0},
        },
    }
    assert client.calls[3][1]["params"]["wellLocation"]["offset"] == {
        "x": 5.0,
        "y": 3.0,
        "z": -3.0,
    }
    assert "move_pipette" in driver.queued.functions


def test_move_pipette_uses_requested_mount_and_its_parked_height(monkeypatch):
    driver, client, _ = make_driver(monkeypatch)
    client.config["loaded_instruments"]["right"] = {"pipette_id": "pipette-right"}
    driver.config["right_pipette_zero"] = 10
    driver.move_to_well("1A1")
    client.calls.clear()

    driver.move_pipette("right", dz=-2)

    assert client.calls[-1][1]["params"] == {
        "pipetteId": "pipette-right",
        "labwareId": "labware-1",
        "wellName": "A1",
        "wellLocation": {
            "origin": "top",
            "offset": {"x": 0.0, "y": 0.0, "z": 8.0},
        },
    }


def test_move_pipette_requires_a_prior_well_target(monkeypatch):
    driver, _, _ = make_driver(monkeypatch)

    with pytest.raises(ValueError, match="move_to_well first"):
        driver.move_pipette(dx=1)


def test_proxy_rejects_unknown_owner_labware_or_pipette(monkeypatch):
    driver, client, _ = make_driver(monkeypatch)
    client.config["loaded_instruments"] = {}

    with pytest.raises(ValueError, match="no loaded labware.*no pipette"):
        driver.move_to_well("1A1")
