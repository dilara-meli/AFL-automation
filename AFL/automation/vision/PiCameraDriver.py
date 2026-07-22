"""A server-facing driver for Raspberry Pi cameras using Picamera2."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import socket
import threading
import time
import uuid

import lazy_loader as lazy

from AFL.automation.APIServer.Driver import Driver


class PiCameraDriver(Driver):
    """Capture still images from a Raspberry Pi camera.

    ``picamera2`` is loaded only when a real camera is constructed, so this
    module can be imported on development machines and in test environments
    without Raspberry Pi camera support.  A camera-like object may be supplied
    to the constructor for testing; it must provide ``capture_array`` and/or
    ``capture_file``.
    """

    defaults = {
        "resolution": [1920, 1080],
        "warmup_seconds": 1.0,
        "output_dir": "camera_images",
        "image_format": "jpg",
        "stream_bitrate": 10_000_000,
    }

    def __init__(self, camera=None, overrides=None):
        """Create the driver and initialize a real Picamera2 when needed."""
        Driver.__init__(
            self,
            name="PiCameraDriver",
            defaults=self.gather_defaults(),
            overrides=overrides,
        )
        self.camera = camera
        self._owns_camera = camera is None
        self._last_image = None
        self._last_saved_path = None
        self._stream_server = None
        self._stream_client = None
        self._stream_thread = None
        self._stream_stop_event = None
        self._stream_address = None
        self._stream_lock = threading.Lock()

        if self.camera is None:
            self.camera = self._create_camera()

    def _create_camera(self):
        """Configure and start a Picamera2 still-camera stream."""
        picamera2 = lazy.load("picamera2", require="AFL-automation[picamera]")
        camera = picamera2.Picamera2()
        resolution = self._resolution()
        configuration = camera.create_still_configuration(main={"size": resolution})
        camera.configure(configuration)
        camera.start()
        warmup_seconds = float(self.config["warmup_seconds"])
        if warmup_seconds > 0:
            time.sleep(warmup_seconds)
        return camera

    def _resolution(self):
        resolution = self.config["resolution"]
        if not isinstance(resolution, (list, tuple)) or len(resolution) != 2:
            raise ValueError("resolution must be a two-item [width, height] sequence")
        width, height = (int(value) for value in resolution)
        if width <= 0 or height <= 0:
            raise ValueError("resolution values must be positive")
        return width, height

    def _output_path(self, filename=None):
        output_dir = Path(self.config["output_dir"]).expanduser()
        if not output_dir.is_absolute():
            output_dir = self.path / output_dir
        output_dir.mkdir(parents=True, exist_ok=True)

        image_format = str(self.config["image_format"]).lstrip(".")
        if not image_format:
            raise ValueError("image_format must not be empty")
        if filename is None:
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
            filename = f"image_{timestamp}.{image_format}"
        else:
            filename = Path(filename)
            if filename.name != str(filename):
                raise ValueError("filename must be a basename, not a path")
            if not filename.suffix:
                filename = filename.with_suffix(f".{image_format}")

        return output_dir / filename

    @Driver.queued()
    def capture(self, save=False, filename=None):
        """Take one image, optionally saving it to the configured output directory.

        When ``save`` is false, the captured image is placed in the driver's
        dropbox and the returned ``image_uid`` can be retrieved through the
        API server.  When ``save`` is true, Picamera2 writes the image directly
        and the returned ``path`` identifies the saved file.
        """
        if save:
            output_path = self._output_path(filename)
            self.camera.capture_file(str(output_path))
            self._last_saved_path = output_path
            return {"saved": True, "path": str(output_path)}

        image = self.camera.capture_array("main")
        self._last_image = image
        if self.dropbox is None:
            self.dropbox = {}
        image_uid = "DB-" + str(uuid.uuid4())
        self.dropbox[image_uid] = image

        return {
            "saved": False,
            "image_uid": image_uid,
            "shape": [int(dimension) for dimension in image.shape],
            "dtype": str(image.dtype),
        }

    @staticmethod
    def _parse_stream_address(address):
        """Parse a TCP listener address supplied as ``host:port``."""
        if not isinstance(address, str):
            raise ValueError("address must be a host:port string")
        address = address.removeprefix("tcp://")
        if address.startswith("["):
            host, separator, port = address[1:].partition("]:")
        else:
            host, separator, port = address.rpartition(":")
        if not separator or not host:
            raise ValueError("address must have the form host:port, e.g. 0.0.0.0:8000")
        try:
            port = int(port)
        except ValueError as exc:
            raise ValueError("stream port must be an integer") from exc
        if not 0 <= port <= 65535:
            raise ValueError("stream port must be between 0 and 65535")
        return host, port

    def _serve_stream_client(self):
        """Accept one TCP client and feed it an H.264 stream until stopped."""
        try:
            self._stream_server.settimeout(0.25)
            while not self._stream_stop_event.is_set():
                try:
                    client, _ = self._stream_server.accept()
                    break
                except TimeoutError:
                    continue
                except OSError:
                    return
            else:
                return

            with self._stream_lock:
                self._stream_client = client

            picamera2_encoders = lazy.load(
                "picamera2.encoders", require="AFL-automation[picamera]"
            )
            picamera2_outputs = lazy.load(
                "picamera2.outputs", require="AFL-automation[picamera]"
            )
            encoder = picamera2_encoders.H264Encoder(
                bitrate=int(self.config["stream_bitrate"])
            )
            output = picamera2_outputs.FileOutput(client.makefile("wb"))
            self.camera.start_encoder(encoder, output, name="main")
            self._stream_stop_event.wait()
            self.camera.stop_encoder(encoder)
        except Exception as exc:
            self.log_error(f"Camera stream failed: {exc}")
        finally:
            with self._stream_lock:
                if self._stream_client is not None:
                    self._stream_client.close()
                self._stream_client = None

    @Driver.queued()
    def start_streaming(self, address):
        """Start an H.264 TCP stream listener at ``address``.

        ``address`` must be ``host:port`` (for example ``0.0.0.0:8000``).
        The driver listens immediately, and begins streaming only after one
        client connects.  It does not start automatically during driver setup.
        """
        host, port = self._parse_stream_address(address)
        with self._stream_lock:
            if self._stream_thread is not None and self._stream_thread.is_alive():
                raise RuntimeError("A video stream is already running")

            server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                server.bind((host, port))
                server.listen(1)
            except Exception:
                server.close()
                raise

            self._stream_server = server
            self._stream_address = f"{server.getsockname()[0]}:{server.getsockname()[1]}"
            self._stream_stop_event = threading.Event()
            self._stream_thread = threading.Thread(
                target=self._serve_stream_client,
                name="PiCameraDriver-stream",
                daemon=True,
            )
            self._stream_thread.start()

        return {"streaming": True, "address": self._stream_address}

    @Driver.queued()
    def stop_streaming(self):
        """Stop the active video stream listener and disconnect its client."""
        with self._stream_lock:
            if self._stream_stop_event is None:
                return {"streaming": False}
            self._stream_stop_event.set()
            if self._stream_client is not None:
                try:
                    self._stream_client.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                self._stream_client.close()
            if self._stream_server is not None:
                self._stream_server.close()
            stream_thread = self._stream_thread

        if stream_thread is not None:
            stream_thread.join(timeout=2)
        with self._stream_lock:
            self._stream_server = None
            self._stream_client = None
            self._stream_thread = None
            self._stream_stop_event = None
            self._stream_address = None
        return {"streaming": False}

    @Driver.queued()
    def close(self):
        """Stop and close a camera created by this driver."""
        self.stop_streaming()
        if self.camera is not None and self._owns_camera:
            if hasattr(self.camera, "stop"):
                self.camera.stop()
            if hasattr(self.camera, "close"):
                self.camera.close()
        self.camera = None
        return {"closed": True}

    def status(self):
        """Return cheap, serializable camera state."""
        return {
            "connected": self.camera is not None,
            "resolution": list(self._resolution()),
            "last_saved_path": (
                str(self._last_saved_path) if self._last_saved_path is not None else None
            ),
            "last_image_available": self._last_image is not None,
            "streaming": (
                self._stream_thread is not None and self._stream_thread.is_alive()
            ),
            "stream_address": self._stream_address,
        }
