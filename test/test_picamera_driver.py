from pathlib import Path
import importlib
import threading

import numpy as np
import pytest

from AFL.automation.vision.PiCameraDriver import PiCameraDriver


picamera_module = importlib.import_module("AFL.automation.vision.PiCameraDriver")


class FakeCamera:
    def __init__(self):
        self.saved_paths = []

    def capture_array(self, stream_name):
        assert stream_name == "main"
        return np.zeros((2, 3, 3), dtype=np.uint8)

    def capture_file(self, path):
        self.saved_paths.append(path)
        Path(path).write_bytes(b"fake image")


def test_capture_places_unsaved_image_in_dropbox():
    driver = PiCameraDriver(camera=FakeCamera())

    result = driver.capture()

    assert result["saved"] is False
    assert result["shape"] == [2, 3, 3]
    assert result["dtype"] == "uint8"
    assert np.array_equal(driver.dropbox[result["image_uid"]], np.zeros((2, 3, 3), dtype=np.uint8))


def test_capture_saves_to_configured_directory(tmp_path):
    camera = FakeCamera()
    driver = PiCameraDriver(
        camera=camera,
        overrides={"output_dir": str(tmp_path), "image_format": "png"},
    )

    result = driver.capture(save=True, filename="sample")

    assert result == {"saved": True, "path": str(tmp_path / "sample.png")}
    assert (tmp_path / "sample.png").read_bytes() == b"fake image"


def test_capture_rejects_path_in_filename(tmp_path):
    driver = PiCameraDriver(camera=FakeCamera(), overrides={"output_dir": str(tmp_path)})

    with pytest.raises(ValueError, match="basename"):
        driver.capture(save=True, filename="nested/sample.jpg")


class FakeStreamServer:
    def setsockopt(self, *args):
        pass

    def bind(self, address):
        self.address = address

    def listen(self, count):
        self.count = count

    def getsockname(self):
        return ("127.0.0.1", 8000)

    def settimeout(self, timeout):
        self.timeout = timeout

    def accept(self):
        threading.Event().wait(0.001)
        raise TimeoutError

    def close(self):
        pass


def test_start_and_stop_streaming_uses_default_address(monkeypatch, capsys):
    monkeypatch.setattr(picamera_module.socket, "socket", lambda *args: FakeStreamServer())
    driver = PiCameraDriver(camera=FakeCamera())

    result = driver.start_streaming()

    assert result["streaming"] is True
    assert result["address"] == "127.0.0.1:8000"
    assert "ffplay -fflags nobuffer" in result["viewer_command"]
    assert result["viewer_command"] in capsys.readouterr().out
    assert driver.status()["streaming"] is True
    assert driver.stop_streaming() == {"streaming": False}
    assert driver.status()["streaming"] is False
