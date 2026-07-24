import numpy as np

from AFL.automation.APIServer.Driver import Driver
from AFL.automation.manipulate.OT2GantryDriver import OT2GantryDriver
from AFL.automation.vision.OT2RidingCam import OT2RidingCam
from AFL.automation.vision.PiCameraDriver import PiCameraDriver


class FakeCamera:
    def capture_array(self, stream_name):
        assert stream_name == "main"
        return np.zeros((2, 3, 3), dtype=np.uint8)


class FakeOT2PrepareClient:
    url = "http://ot2-prepare.test:5002"

    def __init__(self):
        self.calls = []

    def get_config(self, name, print_console, interactive):
        self.calls.append(("get_config", name))
        return {
            "exit_state": "Success!",
            "return_val": {
                "loaded_labware": {
                    "1": (
                        "labware-1",
                        "plate",
                        {"definition": {"dimensions": {"z": 10}}},
                    )
                },
                "loaded_instruments": {"left": {"pipette_id": "pipette-left"}},
            },
        }

    def enqueue(self, **kwargs):
        self.calls.append(("enqueue", kwargs))
        return "owner-task"


def test_ot2_riding_cam_inherits_camera_and_gantry_interfaces(monkeypatch):
    owner_client = FakeOT2PrepareClient()
    monkeypatch.setattr(
        "AFL.automation.manipulate.OT2GantryDriver.Client",
        lambda **kwargs: owner_client,
    )
    driver = OT2RidingCam(
        camera=FakeCamera(),
        ot2_prepare_ip="ot2-prepare.test",
        ot2_prepare_port=5002,
    )

    assert isinstance(driver, Driver)
    assert isinstance(driver, PiCameraDriver)
    assert isinstance(driver, OT2GantryDriver)
    assert driver.name == "OT2RidingCam"
    assert driver.config["resolution"] == [1920, 1080]
    assert driver.config["ot2_prepare_ip"] == "ot2-prepare.test"
    assert driver.config["ot2_prepare_port"] == "5002"
    assert driver.config["gantry_reference_mount"] == "left"

    assert driver.move_to_well("1A1")["owner_task_uuids"] == ["owner-task"]
    capture = driver.capture()
    assert driver.measure_mean_rgb(capture["image_uid"]) == {
        "avg_rgb": {"R": 0.0, "G": 0.0, "B": 0.0}
    }
    assert "move_to_well" in driver.queued.functions
    assert "capture" in driver.queued.functions
    assert "measure_turbidity" in driver.unqueued.functions
