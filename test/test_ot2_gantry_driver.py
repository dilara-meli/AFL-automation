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

    result = driver.move_to_well("1a2", mount="left", offset_x=1.5, offset_z=2)

    assert seen == [("ot2-prepare.test", "5002", "OT2GantryDriver")]
    assert result == {
        "owner_task_uuids": ["owner-task-1", "owner-task-2"],
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
                        "offset": {"x": 1.5, "y": 0.0, "z": 52.0},
                    },
                },
                "interactive": False,
            },
        ),
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


def test_relative_move_uses_current_ot2_prepare_ids_without_safe_reapproach(monkeypatch):
    driver, client, _ = make_driver(monkeypatch)
    driver.move_to_well("1A1")
    client.calls.clear()

    result = driver.move_pipette_down(distance=2)

    assert result["owner_task_uuids"] == ["owner-task-1"]
    assert client.calls[-1][1]["params"]["wellLocation"] == {
        "origin": "top",
        "offset": {"x": 0.0, "y": 0.0, "z": -2.0},
    }


def test_proxy_rejects_unknown_owner_labware_or_pipette(monkeypatch):
    driver, client, _ = make_driver(monkeypatch)
    client.config["loaded_instruments"] = {}

    with pytest.raises(ValueError, match="no loaded labware.*no pipette"):
        driver.move_to_well("1A1")


def test_relative_move_requires_a_prior_target(monkeypatch):
    driver, _, _ = make_driver(monkeypatch)

    with pytest.raises(ValueError, match="move_to_well first"):
        driver.move_pipette_up()
