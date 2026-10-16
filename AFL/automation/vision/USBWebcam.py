"""OpenCV USB webcam with shared live viewing, still capture, and recording."""

from __future__ import annotations

from datetime import datetime, timezone
import math
from pathlib import Path
import os
import threading
import time
import uuid
from urllib.parse import urlencode

from flask import Response, abort, send_file, url_for
import lazy_loader as lazy

from AFL.automation.APIServer.Driver import Driver
from AFL.automation.vision.ImageProcessing import ImageProcessing


class USBWebcam(ImageProcessing, Driver):
    """Share one camera between browser viewers and queued recording commands.

    An injected ``camera`` implements OpenCV's read/isOpened interface and is
    not released by this driver. ``opencv`` may be injected for offline tests.
    Construction does not open hardware. Camera settings take effect on open;
    worker settings take effect when the server starts.
    """

    defaults = {
        "camera_index": 0,
        "resolution": [1280, 720],
        "fps": 30,
        "warmup_seconds": 0.2,
        "jpeg_quality": 80,
        "output_dir": "camera_videos",
        "image_format": "jpg",
        "frame_timeout": 5.0,
        "server_threads": 8,
        "max_viewers": 3,
    }

    def __init__(self, camera=None, overrides=None, name="USBWebcam",
                 initialize_driver=True, opencv=None):
        if initialize_driver:
            Driver.__init__(self, name=name, defaults=self.gather_defaults(),
                            overrides=overrides, useful_links={"USB webcam": "/camera_view"})
        self.camera = camera
        self._owns_camera = camera is None
        self._opencv = opencv
        self._condition = threading.Condition()
        self._capture_stop = threading.Event()
        self._capture_thread = None
        self._record_stop = threading.Event()
        self._record_thread = None
        self._recording = None
        self._last_recording = None
        self._last_saved_path = None
        self._background_image = None
        self._last_image = None
        self._frame = None
        self._jpeg = None
        self._sequence = 0
        self._frame_time = None
        self._error = None
        self._viewers = 0
        self._streaming = False
        self._actual = None
        self._server_threads = None

    def _cv(self):
        if self._opencv is None:
            self._opencv = lazy.load("cv2", require="AFL-automation[vision]")
        return self._opencv

    def _settings(self):
        resolution = self.config["resolution"]
        if not isinstance(resolution, (list, tuple)) or len(resolution) != 2:
            raise ValueError("resolution must be [width, height]")
        if any(isinstance(v, bool) or int(v) != v or v <= 0 for v in resolution):
            raise ValueError("resolution must contain positive integers")
        fps = float(self.config["fps"])
        timeout = float(self.config["frame_timeout"])
        warmup = float(self.config["warmup_seconds"])
        quality = self.config["jpeg_quality"]
        if not math.isfinite(fps) or fps <= 0:
            raise ValueError("fps must be finite and positive")
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("frame_timeout must be finite and positive")
        if not math.isfinite(warmup) or warmup < 0:
            raise ValueError("warmup_seconds must be finite and nonnegative")
        if isinstance(quality, bool) or int(quality) != quality or not 1 <= quality <= 100:
            raise ValueError("jpeg_quality must be an integer from 1 to 100")
        threads, viewers = self.config["server_threads"], self.config["max_viewers"]
        if any(isinstance(v, bool) or int(v) != v for v in (threads, viewers)):
            raise ValueError("server_threads and max_viewers must be integers")
        effective_threads = self._server_threads if self._server_threads is not None else threads
        if viewers < 1 or effective_threads < viewers + 2:
            raise ValueError("server_threads must leave two workers beyond max_viewers")
        return tuple(map(int, resolution)), fps, timeout, warmup, int(quality)

    @Driver.queued()
    def open(self):
        """Open the camera and wait for its first usable frame."""
        resolution, fps, timeout, warmup, quality = self._settings()
        if self._capture_thread is not None and self._capture_thread.is_alive():
            if self._capture_stop.is_set() or self._error:
                raise RuntimeError("Previous camera worker has not stopped; close before reopening")
            return self.get_camera_status()
        cv = self._cv()
        if self.camera is None:
            self.camera = cv.VideoCapture(self.config["camera_index"])
        if not self.camera.isOpened():
            if self._owns_camera:
                self.camera.release()
                self.camera = None
            raise RuntimeError(f"Cannot open camera {self.config['camera_index']!r}")
        try:
            if self._owns_camera:
                self.camera.set(cv.CAP_PROP_FRAME_WIDTH, resolution[0])
                self.camera.set(cv.CAP_PROP_FRAME_HEIGHT, resolution[1])
                self.camera.set(cv.CAP_PROP_FPS, fps)
            with self._condition:
                self._frame = self._jpeg = None
                self._error = None
                self._actual = None
                self._capture_stop.clear()
            self._capture_thread = threading.Thread(
                target=self._capture_loop, args=(fps, warmup, quality),
                name=f"{self.name}-capture", daemon=True)
            self._capture_thread.start()
            self._fresh_frame(timeout)
        except Exception:
            self._stop_capture()
            if self._owns_camera and self.camera is not None:
                self.camera.release()
                self.camera = None
            raise
        return self.get_camera_status()

    def _capture_loop(self, fps, warmup, quality):
        try:
            if self._capture_stop.wait(warmup):
                return
            while not self._capture_stop.is_set():
                started = time.monotonic()
                ok, frame = self.camera.read()
                if not ok or frame is None:
                    raise RuntimeError("Camera frame read failed; check USB connection")
                cv = self._cv()
                ok, jpeg = cv.imencode(".jpg", frame, [cv.IMWRITE_JPEG_QUALITY, quality])
                if not ok:
                    raise RuntimeError("Camera JPEG encoding failed")
                with self._condition:
                    self._frame = frame.copy()  # OpenCV BGR; convert only for dropbox images.
                    self._jpeg = jpeg.tobytes()
                    self._sequence += 1
                    self._frame_time = time.monotonic()
                    actual_fps = float(self.camera.get(cv.CAP_PROP_FPS))
                    self._actual = {"resolution": [frame.shape[1], frame.shape[0]],
                                    "fps": actual_fps if math.isfinite(actual_fps) and actual_fps > 0 else None}
                    self._condition.notify_all()
                self._capture_stop.wait(max(0, 1 / fps - (time.monotonic() - started)))
        except Exception as exc:
            with self._condition:
                self._error = str(exc)
            self.log_error(f"Webcam capture failed: {exc}")
            self._record_stop.set()
        finally:
            if self._owns_camera and self.camera is not None:
                self.camera.release()
                self.camera = None
            with self._condition:
                self._capture_stop.set()
                self._condition.notify_all()

    def _fresh_frame(self, timeout=None):
        timeout = float(self.config["frame_timeout"]) if timeout is None else timeout
        deadline = time.monotonic() + timeout
        with self._condition:
            while self._frame is None and not self._error and not self._capture_stop.is_set():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeError("Timed out waiting for a camera frame")
                self._condition.wait(remaining)
            if self._error:
                raise RuntimeError(self._error)
            if self._frame is None or self._capture_stop.is_set():
                raise RuntimeError("Camera is closed")
            if time.monotonic() - self._frame_time > timeout:
                raise RuntimeError("Camera frame is stale")
            return self._frame.copy()

    def _output_dir(self):
        directory = Path(self.config["output_dir"]).expanduser()
        if not directory.is_absolute():
            directory = self.path / directory
        return directory.resolve()

    def _output_path(self, filename, suffix):
        directory = self._output_dir()
        if filename is None:
            filename = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ") + suffix
        if not isinstance(filename, str) or not filename or Path(filename).name != filename or filename.startswith(".") or "\\" in filename:
            raise ValueError("filename must be a non-hidden basename")
        if not Path(filename).suffix:
            filename += suffix
        if Path(filename).suffix.lower() != suffix:
            raise ValueError(f"filename must end in {suffix}")
        path = directory / filename
        if path.exists() or path.is_symlink():
            raise FileExistsError(f"Output already exists: {filename}")
        directory.mkdir(parents=True, exist_ok=True)
        return path

    @Driver.queued()
    def capture(self, save=False, filename=None):
        """Capture RGB pixels into the dropbox, or save an image on the server."""
        suffix = "." + str(self.config["image_format"]).lstrip(".").lower()
        if suffix not in (".jpg", ".jpeg", ".png"):
            raise ValueError("image_format must be jpg, jpeg, or png")
        path = self._output_path(filename, suffix) if save else None
        self.open()
        frame = self._fresh_frame()
        if save:
            if not self._cv().imwrite(str(path), frame):
                raise RuntimeError("Image write failed")
            self._last_saved_path = str(path)
            return {"saved": True, "path": str(path)}
        image = frame[..., ::-1].copy()
        self._last_image = image
        if self.dropbox is None:
            self.dropbox = {}
        uid = "DB-" + str(uuid.uuid4())
        self.dropbox[uid] = image
        return {"saved": False, "image_uid": uid, "shape": list(image.shape), "dtype": str(image.dtype)}

    @Driver.queued()
    def start_streaming(self):
        """Enable browser viewing; opening a read-only route never starts capture."""
        self.open()
        with self._condition:
            self._streaming = True
        return {"streaming": True, "viewer_url": "/camera_view", "feed_url": "/camera_feed"}

    @Driver.queued()
    def stop_streaming(self):
        """Disconnect viewers and release the camera unless a recording is active."""
        if self._record_thread is not None and self._record_thread.is_alive():
            raise RuntimeError("Stop recording before stopping streaming")
        self._stop_capture()
        return {"streaming": False}

    def _stop_capture(self):
        with self._condition:
            self._streaming = False
            self._capture_stop.set()
            self._condition.notify_all()
        if self._capture_thread is not None:
            self._capture_thread.join(timeout=float(self.config["frame_timeout"]) + 1)
            if self._capture_thread.is_alive():
                raise RuntimeError("Camera worker did not stop; device read may be blocked")
            self._capture_thread = None
        with self._condition:
            self._frame = self._jpeg = None

    @Driver.queued()
    def start_recording(self, filename=None):
        """Start background recording; its final result is returned by stop_recording."""
        if self._record_thread is not None and self._record_thread.is_alive():
            raise RuntimeError("A recording is already active")
        path = self._output_path(filename, ".avi")
        self.open()
        frame = self._fresh_frame()
        fps = float(self.config["fps"])
        partial = path.with_name(f".{path.stem}.{uuid.uuid4().hex}.partial.avi")
        cv = self._cv()
        writer = cv.VideoWriter(str(partial), cv.VideoWriter_fourcc(*"MJPG"), fps,
                                (frame.shape[1], frame.shape[0]))
        if not writer.isOpened():
            writer.release()
            raise RuntimeError("Cannot open MJPEG AVI writer; check codec and output directory")
        with self._condition:
            self._record_stop.clear()
            self._recording = {"recording": True, "filename": path.name, "path": str(path),
                               "download_url": "/download_recording?" + urlencode({"filename": path.name}),
                               "frame_count": 0, "duration": 0.0, "completed": False, "error": None}
            self._last_recording = None
            self._record_thread = threading.Thread(
                target=self._record_loop, args=(writer, partial, path, fps),
                name=f"{self.name}-record", daemon=True)
            self._record_thread.start()
            return dict(self._recording)

    def _record_loop(self, writer, partial, path, fps):
        started = time.monotonic()
        count = 0
        error = None
        shape = None
        try:
            while not self._record_stop.is_set():
                frame = self._fresh_frame()
                if shape is not None and frame.shape != shape:
                    raise RuntimeError("Camera frame dimensions changed during recording")
                shape = frame.shape
                writer.write(frame)
                count += 1
                with self._condition:
                    self._recording.update(frame_count=count, duration=time.monotonic() - started)
                self._record_stop.wait(max(0, started + count / fps - time.monotonic()))
            if self._error:
                raise RuntimeError(self._error)
            if count == 0:
                raise RuntimeError("Recording stopped before any frames were written")
        except Exception as exc:
            error = str(exc)
            self.log_error(f"Webcam recording failed: {exc}")
        finally:
            try:
                writer.release()
                if error is None:
                    if not partial.is_file() or partial.stat().st_size == 0:
                        raise RuntimeError("Video writer produced no file")
                    # Publish without replacing a file created by another process.
                    os.link(partial, path)
                    partial.unlink()
            except Exception as exc:
                error = str(exc)
            with self._condition:
                self._recording.update(recording=False, completed=error is None, error=error,
                                       frame_count=count, duration=time.monotonic() - started)
                self._last_recording = dict(self._recording)
                self._recording = None
                if error is None:
                    self._last_saved_path = str(path)
                self._condition.notify_all()
            self._record_stop.set()

    @Driver.queued()
    def stop_recording(self):
        """Finalize an active recording and return its result; safe to repeat."""
        self._record_stop.set()
        if self._record_thread is not None:
            self._record_thread.join(timeout=float(self.config["frame_timeout"]) + 1)
            if self._record_thread.is_alive():
                raise RuntimeError("Video writer did not stop; finalization is still pending")
            self._record_thread = None
        with self._condition:
            result = dict(self._last_recording) if self._last_recording else {"recording": False}
        if result.get("error"):
            raise RuntimeError(f"Recording failed: {result['error']}")
        return result

    @Driver.queued()
    def record_video(self, duration, filename=None):
        """Record for finite positive seconds, blocking this queued task until finalized."""
        duration = float(duration)
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError("duration must be finite and positive seconds")
        self.start_recording(filename)
        self._record_stop.wait(duration)
        return self.stop_recording()

    @Driver.queued()
    def close(self):
        """Finalize recording and stop capture, including when recording has failed."""
        try:
            self.stop_recording()
        finally:
            self._stop_capture()
        return {"closed": True}

    @Driver.unqueued()
    def get_camera_status(self):
        """Return cached camera, viewer, and recording state without hardware I/O."""
        with self._condition:
            return {"connected": bool(self._capture_thread and self._capture_thread.is_alive()
                                      and not self._capture_stop.is_set()),
                    "streaming": self._streaming and not self._capture_stop.is_set(),
                    "viewers": self._viewers, "capture_settings": self._actual,
                    "error": self._error, "last_saved_path": self._last_saved_path,
                    "recording": dict(self._recording) if self._recording else None,
                    "last_recording": dict(self._last_recording) if self._last_recording else None}

    def status(self):
        return self.get_camera_status()

    def validate_server_threads(self, threads):
        """Validate the actual WSGI pool, including explicit run(threads=...) overrides."""
        viewers = self.config["max_viewers"]
        if isinstance(threads, bool) or not isinstance(threads, int) or threads < 1:
            raise ValueError("server threads must be a positive integer")
        if isinstance(viewers, bool) or not isinstance(viewers, int) or viewers < 1 or threads < viewers + 2:
            raise ValueError("server_threads must leave two workers beyond max_viewers")
        self._server_threads = threads

    @Driver.unqueued()
    def list_recordings(self):
        """List finalized recordings, including files saved during earlier sessions."""
        directory = self._output_dir()
        return [{"filename": p.name, "size_bytes": p.stat().st_size}
                for p in sorted(directory.glob("*.avi"))
                if not p.name.startswith(".") and not p.is_symlink() and p.is_file()]

    @Driver.unqueued(render_hint="response")
    def camera_view(self, **kwargs):
        """A read-only browser viewer; start streaming through the normal client first."""
        feed = url_for("camera_feed")
        return Response(f'<!doctype html><html><head><title>USBWebcam</title></head>'
                        f'<body><h1>USBWebcam</h1><img src="{feed}" alt="Live webcam feed" '
                        'style="max-width:100%;height:auto"><p>Start streaming with your AFL client '
                        'before opening this page.</p></body></html>', mimetype="text/html")

    @Driver.unqueued(render_hint="response")
    def camera_feed(self, **kwargs):
        """Stream shared JPEG frames without acquiring a separate camera handle."""
        with self._condition:
            if self._server_threads is not None and int(self.config["max_viewers"]) > self._server_threads - 2:
                abort(503, description="Viewer limit exceeds the running server worker capacity")
            if not self._streaming or self._capture_stop.is_set() or self._error:
                abort(503, description=self._error or "Streaming is not running")
            if self._viewers >= int(self.config["max_viewers"]):
                abort(503, description="Maximum viewers reached")
            self._viewers += 1
            epoch = self._capture_thread
        def frames():
            sequence = -1
            try:
                while True:
                    with self._condition:
                        ready = self._condition.wait_for(
                            lambda: self._sequence != sequence or not self._streaming
                            or self._capture_stop.is_set() or epoch is not self._capture_thread,
                            timeout=float(self.config["frame_timeout"]))
                        if not ready or not self._streaming or self._capture_stop.is_set() or epoch is not self._capture_thread:
                            return
                        jpeg, sequence = self._jpeg, self._sequence
                    yield b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: " + str(len(jpeg)).encode() + b"\r\n\r\n" + jpeg + b"\r\n"
            finally:
                release()
        released = False
        def release():
            nonlocal released
            with self._condition:
                if not released:
                    self._viewers -= 1
                    released = True
        response = Response(frames(), content_type="multipart/x-mixed-replace; boundary=frame",
                            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})
        response.call_on_close(release)
        return response

    @Driver.unqueued(render_hint="response")
    def download_recording(self, filename=None, **kwargs):
        """Download a finalized AVI using a basename restricted to output_dir."""
        if not isinstance(filename, str) or not filename or Path(filename).name != filename or filename.startswith(".") or "\\" in filename or Path(filename).suffix.lower() != ".avi":
            abort(400, description="filename must be an AVI basename")
        path = self._output_dir() / filename
        with self._condition:
            if self._recording and self._recording["path"] == str(path):
                abort(409, description="Recording has not been finalized")
        if path.is_symlink() or not path.is_file():
            abort(404, description="Recording not found")
        return send_file(path, mimetype="video/x-msvideo", as_attachment=True, download_name=filename)


_DEFAULT_CUSTOM_CONFIG = {"_classname": "AFL.automation.vision.USBWebcam.USBWebcam", "overrides": {}}
_DEFAULT_PORT = 5097

if __name__ == "__main__":
    from AFL.automation.shared.launcher import *
