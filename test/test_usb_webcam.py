"""USBWebcam tests use injected hardware only, including route/queue tests."""
import importlib
import json
from pathlib import Path
import threading
import time

import numpy as np
import pytest

from AFL.automation.APIServer import APIServer
from AFL.automation.vision.USBWebcam import USBWebcam


class FakeCamera:
    def __init__(self):
        self.reads = 0
        self.released = False
        self.fail = False

    def isOpened(self):
        return True

    def read(self):
        self.reads += 1
        return (False, None) if self.fail else (True, np.full((2, 3, 3), [10, 20, 30], dtype=np.uint8))

    def get(self, prop):
        return 30.0

    def release(self):
        self.released = True

    def set(self, *args):
        pass


class FakeWriter:
    def __init__(self, path, fail=False, opened=True):
        self.path = Path(path)
        self.fail = fail
        self.opened = opened
        self.released = False
        self.written = threading.Event()
        self.path.write_bytes(b"AVI")

    def isOpened(self):
        return self.opened

    def write(self, frame):
        if self.fail:
            raise OSError("disk full")
        with self.path.open("ab") as stream:
            stream.write(b"FRAME")
        self.written.set()

    def release(self):
        self.released = True


class FakeCV:
    CAP_PROP_FRAME_WIDTH = 1
    CAP_PROP_FRAME_HEIGHT = 2
    CAP_PROP_FPS = 3
    IMWRITE_JPEG_QUALITY = 4

    def __init__(self):
        self.writers = []
        self.fail_write = False
        self.writer_opened = True
        self.encode_ok = True

    def imencode(self, *args):
        return self.encode_ok, np.frombuffer(b"JPEG", dtype=np.uint8)

    def imwrite(self, path, frame):
        Path(path).write_bytes(b"IMAGE")
        return True

    def VideoWriter_fourcc(self, *args):
        return 0

    def VideoWriter(self, path, *args):
        writer = FakeWriter(path, self.fail_write, self.writer_opened)
        self.writers.append(writer)
        return writer


@pytest.fixture
def webcam(tmp_path):
    camera, cv = FakeCamera(), FakeCV()
    driver = USBWebcam(camera=camera, opencv=cv, overrides={
        "output_dir": str(tmp_path / "videos"), "warmup_seconds": 0,
        "frame_timeout": 0.5, "fps": 60})
    yield driver, camera, cv
    try:
        driver.close()
    except RuntimeError:
        pass


@pytest.fixture
def web(webcam):
    driver, _, _ = webcam
    server = APIServer("USBWebcam")
    server.add_standard_routes()
    server.create_queue(driver)
    yield server, server.app.test_client()
    if server.queue_daemon.is_alive():
        # QueueDaemon's legacy terminate calls MutableQueue.put without loc.
        server.queue_daemon.terminate = lambda: setattr(server.queue_daemon, "stop", True)
        server.task_queue.put(None, loc=server.task_queue.qsize())
        server.queue_daemon.join(timeout=2)


def test_construct_and_rgb_capture(webcam):
    driver, camera, _ = webcam
    assert camera.reads == 0
    assert driver.status()["connected"] is False
    result = driver.capture()
    image = driver.dropbox[result["image_uid"]]
    assert image[0, 0].tolist() == [30, 20, 10]
    assert result["dtype"] == "uint8"
    assert driver.rgb_values(image) == {"R": 30, "G": 20, "B": 10}
    assert driver.status()["capture_settings"]["resolution"] == [3, 2]
    assert json.dumps(driver.status())
    saved = driver.capture(save=True, filename="still")
    assert Path(saved["path"]).read_bytes() == b"IMAGE"
    driver.close()
    assert not camera.released


def test_recording_and_streaming_share_capture(webcam):
    driver, camera, cv = webcam
    driver.start_streaming()
    worker = driver._capture_thread
    started = driver.start_recording("sample")
    assert cv.writers[-1].written.wait(1)
    assert not Path(started["path"]).exists()
    with pytest.raises(RuntimeError, match="already active"):
        driver.start_recording()
    with pytest.raises(RuntimeError, match="Stop recording"):
        driver.stop_streaming()
    result = driver.stop_recording()
    assert result["completed"] and result["frame_count"] > 0
    assert Path(result["path"]).read_bytes().startswith(b"AVI")
    assert driver._capture_thread is worker and driver.status()["streaming"]
    assert driver.list_recordings()[0]["filename"] == "sample.avi"
    assert driver.stop_recording() == result
    with pytest.raises(FileExistsError):
        driver.start_recording("sample.avi")
    assert cv.writers[-1].released


@pytest.mark.parametrize("duration", [0, -1, float("inf"), float("nan")])
def test_invalid_duration_has_no_hardware_effect(webcam, duration):
    driver, camera, _ = webcam
    with pytest.raises(ValueError):
        driver.record_video(duration)
    assert camera.reads == 0


@pytest.mark.parametrize("name", ["../x.avi", "/x.avi", "x.mp4", ".hidden.avi", "a\\b.avi", ""])
def test_invalid_recording_filename(webcam, name):
    driver, camera, _ = webcam
    with pytest.raises(ValueError):
        driver.start_recording(name)
    assert camera.reads == 0


def test_timed_recording(webcam):
    driver, _, cv = webcam
    result = driver.record_video(0.06, "timed")
    assert result["completed"] and result["frame_count"] >= 1
    assert result["duration"] >= 0.05
    assert cv.writers[-1].released


def test_recording_write_error_reported(webcam):
    driver, _, cv = webcam
    cv.fail_write = True
    driver.start_recording("failed")
    driver._record_thread.join(1)
    assert driver.status()["last_recording"]["error"] == "disk full"
    with pytest.raises(RuntimeError, match="disk full"):
        driver.stop_recording()
    assert cv.writers[-1].released
    assert driver.list_recordings() == []


def test_camera_disconnect_finalizes_recording(webcam):
    driver, camera, cv = webcam
    driver.start_streaming()
    driver.start_recording()
    assert cv.writers[-1].written.wait(1)
    camera.fail = True
    driver._capture_thread.join(1)
    with pytest.raises(RuntimeError, match="frame read failed"):
        driver.stop_recording()
    assert not driver.status()["connected"]
    assert cv.writers[-1].released


def test_encode_and_writer_open_failure(webcam):
    driver, _, cv = webcam
    cv.encode_ok = False
    with pytest.raises(RuntimeError, match="encoding failed"):
        driver.open()
    cv.encode_ok = True
    cv.writer_opened = False
    with pytest.raises(RuntimeError, match="Cannot open MJPEG"):
        driver.start_recording()
    assert cv.writers[-1].released


def test_feed_limits_and_disconnect(webcam, web):
    driver, _, _ = webcam
    _, client = web
    assert client.get("/camera_feed").status_code == 503
    assert driver.camera.reads == 0
    assert b"USBWebcam" in client.get("/camera_view").data
    driver.start_streaming()
    responses = [client.get("/camera_feed", buffered=False) for _ in range(3)]
    try:
        assert all(r.status_code == 200 for r in responses)
        assert b"Content-Type: image/jpeg" in next(iter(responses[0].response))
        assert client.get("/camera_feed").status_code == 503
        assert driver.status()["viewers"] == 3
        assert client.get("/get_camera_status").json["streaming"]
        driver.stop_streaming()
        assert list(responses[0].response) == []
    finally:
        for response in responses:
            response.close()
    assert driver.status()["viewers"] == 0


def test_download_validation_and_completion(webcam, web):
    driver, _, cv = webcam
    _, client = web
    for name in ("../x.avi", ".hidden.avi", "x.mp4"):
        assert client.get("/download_recording", query_string={"filename": name}).status_code == 400
    assert client.get("/download_recording").status_code == 400
    assert client.get("/download_recording?filename=missing.avi").status_code == 404
    driver.start_recording("download")
    assert cv.writers[-1].written.wait(1)
    assert client.get("/download_recording?filename=download.avi").status_code == 409
    driver.stop_recording()
    response = client.get("/download_recording?filename=download.avi")
    assert response.status_code == 200 and response.data.startswith(b"AVI")
    response.close()
    link = driver._output_dir() / "link.avi"
    link.symlink_to(driver._output_dir() / "download.avi")
    assert client.get("/download_recording?filename=link.avi").status_code == 404


def test_authenticated_queue_success_and_failure(webcam, web):
    driver, _, _ = webcam
    server, client = web
    assert client.post("/enqueue", json={"task_name": "record_video", "duration": 0.05}).status_code == 401
    login = client.post("/login", json={"username": "WebcamTest", "password": "domo_arigato"})
    assert login.status_code == 200
    token = login.json["token"]
    headers = {"Authorization": f"Bearer {token}"}
    server.init()
    for duration, expected in ((0.05, "Success!"), (-1, "Error!")):
        response = client.post("/enqueue", headers=headers,
                               json={"task_name": "record_video", "duration": duration})
        assert response.status_code == 200
        uid = response.text
        deadline = time.monotonic() + 3
        while not any(str(t["uuid"]) == uid for t in server.history):
            assert time.monotonic() < deadline
            threading.Event().wait(0.01)
        task = next(t for t in server.history if str(t["uuid"]) == uid)
        assert task["meta"]["exit_state"] == expected
        if duration > 0:
            assert task["meta"]["return_val"]["completed"]


@pytest.mark.parametrize("threaded", [False, True])
def test_server_worker_configuration(webcam, web, monkeypatch, threaded):
    server, _ = web
    module = importlib.import_module("AFL.automation.APIServer.APIServer")
    monkeypatch.setattr(module, "_ADVERTISE_ZEROCONF", False)
    monkeypatch.setattr(module, "_HAVE_WAITRESS", True)
    monkeypatch.setattr(server, "init", lambda: None)
    calls = []
    monkeypatch.setattr(module, "wsgi_serve", lambda app, **kwargs: calls.append(kwargs))
    if threaded:
        thread = server.run_threaded(use_waitress=True, start_thread=False)
        thread.start()
        thread.join(1)
    else:
        server.run(use_waitress=True)
    assert calls[0]["threads"] == 8


def test_worker_validation(webcam):
    driver, camera, _ = webcam
    driver.config["server_threads"] = 3
    with pytest.raises(ValueError, match="two workers"):
        driver.open()
    assert camera.reads == 0


def test_owned_camera_release_and_reopen(tmp_path):
    cv = FakeCV()
    cameras = []
    def create(index):
        camera = FakeCamera()
        cameras.append(camera)
        return camera
    cv.VideoCapture = create
    driver = USBWebcam(opencv=cv, overrides={"warmup_seconds": 0})
    driver.open()
    driver.close()
    assert cameras[0].released
    driver.open()
    driver.close()
    assert len(cameras) == 2 and cameras[1].released


def test_writer_failure_interrupts_long_timed_recording(webcam):
    driver, _, cv = webcam
    cv.fail_write = True
    started = time.monotonic()
    with pytest.raises(RuntimeError, match="disk full"):
        driver.record_video(60)
    assert time.monotonic() - started < 2


def test_stream_release_without_iteration(webcam, web):
    driver, _, _ = webcam
    server, _ = web
    driver.start_streaming()
    with server.app.test_request_context("/camera_feed"):
        response = driver.camera_feed()
        assert driver.status()["viewers"] == 1
        response.close()
        assert driver.status()["viewers"] == 0


def test_explicit_server_worker_override_rejected(webcam, web, monkeypatch):
    server, _ = web
    module = importlib.import_module("AFL.automation.APIServer.APIServer")
    monkeypatch.setattr(module, "_ADVERTISE_ZEROCONF", False)
    monkeypatch.setattr(module, "_HAVE_WAITRESS", True)
    monkeypatch.setattr(server, "init", lambda: None)
    with pytest.raises(ValueError, match="two workers"):
        server.run(use_waitress=True, threads=1)


def test_module_launcher_does_not_open_hardware(tmp_path):
    import os
    import subprocess
    import sys

    # Redirect PersistentConfig itself: the shared launcher has a legacy global
    # config path outside AFL_HOME. Never touch the user's global configuration.
    script = '''
import os
from pathlib import Path
import runpy
from AFL.automation.shared.PersistentConfig import PersistentConfig
from AFL.automation.APIServer import APIServer
original_init = PersistentConfig.__init__
def isolated_init(self, path, *args, **kwargs):
    return original_init(self, Path(os.environ["AFL_HOME"]) / Path(path).name, *args, **kwargs)
PersistentConfig.__init__ = isolated_init
def inspect_server(self, **kwargs):
    assert self.name == self.driver.name == "USBWebcam"
    assert kwargs["port"] == 5097
    assert self.driver.camera is None
    assert self.driver.status()["connected"] is False
APIServer.run = inspect_server
runpy.run_module("AFL.automation.vision.USBWebcam", run_name="__main__", alter_sys=True)
'''
    environment = dict(os.environ, AFL_HOME=str(tmp_path), MPLCONFIGDIR=str(tmp_path / "mpl"))
    result = subprocess.run([sys.executable, "-c", script], env=environment,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


def test_query_driver_preserves_stream_response(webcam, web):
    driver, _, _ = webcam
    _, client = web
    driver.start_streaming()
    response = client.get("/query_driver", json={"r": "camera_feed"}, buffered=False)
    try:
        assert response.status_code == 200
        assert response.mimetype == "multipart/x-mixed-replace"
        assert b"JPEG" in next(iter(response.response))
    finally:
        response.close()
    assert driver.status()["viewers"] == 0


def test_close_finalizes_active_recording(webcam):
    driver, _, cv = webcam
    driver.start_streaming()
    driver.start_recording("closed")
    assert cv.writers[-1].written.wait(1)
    assert driver.close() == {"closed": True}
    state = driver.status()
    assert state["last_recording"]["completed"]
    assert not state["connected"] and not state["streaming"]
    assert cv.writers[-1].released
