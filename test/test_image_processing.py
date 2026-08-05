import numpy as np
import pytest
import matplotlib.pyplot as plt

from AFL.automation.APIServer.Driver import Driver
from AFL.automation.shared.samplecells import NeutronSampleCell
from AFL.automation.vision.ImageProcessing import ImageProcessing
from AFL.automation.vision.PiCameraDriver import PiCameraDriver
from AFL.automation.vision.RGBCamera import RGBCamera, _DEFAULT_CUSTOM_CONFIG


class FakeCamera:
    def capture_array(self, stream_name):
        return np.zeros((2, 3, 3), dtype=np.uint8)


class FakeOpenCVCapture:
    def __init__(self, camera_index):
        self.camera_index = camera_index
        self.released = False

    def isOpened(self):
        return not self.released

    def release(self):
        self.released = True


class FakeCV2:
    def __init__(self):
        self.captures = []

    def VideoCapture(self, camera_index):
        capture = FakeOpenCVCapture(camera_index)
        self.captures.append(capture)
        return capture


def test_image_processing_crops_circular_region_and_calculates_rgb():
    processor = ImageProcessing()
    image = np.zeros((5, 5, 3), dtype=np.uint8)
    image[..., 0] = 10
    image[..., 1] = 20
    image[..., 2] = 30

    circle = processor.crop_to_circle(image, center=(2, 2), radius=2)

    assert circle["mask"].shape == (5, 5)
    assert not circle["mask"][0, 0]
    assert circle["mask"][2, 2]
    assert processor.rgb_values(image, circle["mask"]) == {"R": 10.0, "G": 20.0, "B": 30.0}


def test_image_processing_background_normalizes_turbidity():
    processor = ImageProcessing()
    background = np.full((2, 2), 100, dtype=np.uint8)
    image = np.full((2, 2), 50, dtype=np.uint8)

    result = processor.turbidity_measurement(image, background)

    assert result["turbidity_metric"] == pytest.approx(151 / 201)


def test_picamera_exposes_image_processing_operations():
    driver = PiCameraDriver(camera=FakeCamera())
    image = np.zeros((3, 3, 3), dtype=np.uint8)
    image[..., 0] = 10
    image[..., 1] = 20
    image[..., 2] = 30
    driver.dropbox = {"image": image, "background": image * 2}

    assert driver.measure_mean_rgb("image")["avg_rgb"] == {"R": 10.0, "G": 20.0, "B": 30.0}
    driver.set_background("background")
    # The legacy normalization uses a background-derived pedestal.
    assert driver.measure_turbidity("image")["turbidity_metric"] == pytest.approx(0.7533163536028865)
    assert "measure_mean_rgb" in driver.unqueued.functions
    assert "measure_turbidity" in driver.unqueued.functions
    assert "set_background" in driver.queued.functions


def test_picamera_measurements_apply_rectangular_crop_only_when_both_bounds_given():
    driver = PiCameraDriver(camera=FakeCamera())
    image = np.zeros((4, 4, 3), dtype=np.uint8)
    image[1:3, 1:3] = [10, 20, 30]
    background = np.full((4, 4, 3), 100, dtype=np.uint8)
    sample = background.copy()
    sample[1:3, 1:3] = 50
    driver.dropbox = {"image": image, "sample": sample, "background": background}

    assert driver.measure_mean_rgb(
        "image", row_crop=[1, 3], col_crop=[1, 3]
    )["avg_rgb"] == {"R": 10.0, "G": 20.0, "B": 30.0}
    assert driver.measure_mean_rgb("image", row_crop=[1, 3])["avg_rgb"] == {
        "R": 2.5,
        "G": 5.0,
        "B": 7.5,
    }

    cropped_turbidity = driver.measure_turbidity(
        "sample",
        background_uid="background",
        row_crop=[1, 3],
        col_crop=[1, 3],
    )
    assert cropped_turbidity["turbidity_metric"] == pytest.approx(151 / 201)


def test_rgb_camera_uses_image_processing_mixin_and_vision_loader(monkeypatch):
    driver = RGBCamera(overrides={
        "background_capture_on_init": False,
        "px_crop": [1, 5],
        "py_crop": [1, 5],
    })
    image = np.zeros((6, 6, 3), dtype=np.uint8)
    image[..., 0] = 30  # B
    image[..., 1] = 20  # G
    image[..., 2] = 10  # R

    monkeypatch.setattr(driver, "find_circular_region", lambda image, radii: (3, 3, 2))
    processed = driver._process_image(image, px_crop=[1, 5], py_crop=[1, 5], hough_radii=2)
    configured_crop = driver._process_image(image, hough_radii=2)

    assert isinstance(driver, (NeutronSampleCell, ImageProcessing, Driver))
    assert processed["avg_rgb"] == {"R": 10.0, "G": 20.0, "B": 30.0}
    assert processed["mask"].shape == (4, 4)
    assert configured_crop["mask"].shape == (4, 4)
    assert "measure_mean_rgb" in driver.unqueued.functions
    assert "set_background" in driver.queued.functions
    assert RGBCamera.__module__ == "AFL.automation.vision.RGBCamera"
    assert _DEFAULT_CUSTOM_CONFIG["_classname"] == "AFL.automation.vision.RGBCamera.RGBCamera"
    assert "px_crop" in driver.config
    assert "py_crop" in driver.config
    assert "row_crop" not in driver.config
    assert "col_crop" not in driver.config
    assert "row_crop" not in _DEFAULT_CUSTOM_CONFIG["overrides"]
    assert "col_crop" not in _DEFAULT_CUSTOM_CONFIG["overrides"]


def test_rgb_camera_opens_on_initialization_and_can_be_closed(monkeypatch):
    fake_cv2 = FakeCV2()
    monkeypatch.setattr(
        "AFL.automation.vision.RGBCamera.lazy.load", lambda *args, **kwargs: fake_cv2
    )

    driver = RGBCamera(overrides={
        "background_capture_on_init": False,
        "camera_index": 4,
    })

    assert len(fake_cv2.captures) == 1
    assert driver._opencv_capture is fake_cv2.captures[0]
    assert driver._opencv_capture.camera_index == 4
    assert "open" in driver.queued.functions
    assert "close" in driver.queued.functions
    assert driver.open() == {"camera_index": 4, "opened": True}
    assert len(fake_cv2.captures) == 1

    assert driver.close() == {"closed": True}
    assert fake_cv2.captures[0].released is True
    assert driver._opencv_capture is None

    assert driver.open() == {"camera_index": 4, "opened": True}
    assert len(fake_cv2.captures) == 2


def test_rgb_camera_initializes_without_the_optional_opencv_dependency(monkeypatch):
    def missing_cv2(*args, **kwargs):
        raise ModuleNotFoundError("No module named 'cv2'")

    monkeypatch.setattr("AFL.automation.vision.RGBCamera.lazy.load", missing_cv2)

    driver = RGBCamera(overrides={"background_capture_on_init": False})

    assert driver._opencv_capture is None
    with pytest.raises(ImportError, match="opencv-python"):
        driver.open()


def test_neutron_sample_cell_extracts_shared_crop_and_circle(monkeypatch):
    cell = NeutronSampleCell()
    image = np.zeros((6, 6, 3), dtype=np.uint8)

    monkeypatch.setattr(cell, "find_circular_region", lambda image, radii: (2, 2, 1))
    sample = cell.extract_sample_image(
        image,
        row_crop=[1, 5],
        col_crop=[1, 5],
        hough_radii=1,
        color_order="BGR",
    )

    assert sample["cropped_img"].shape == (4, 4, 3)
    assert sample["mask"].shape == (4, 4)
    assert sample["row_crop"] == [1, 5]
    assert sample["col_crop"] == [1, 5]


def test_rgb_geometry_plot_keeps_full_image_pixel_axes(monkeypatch, tmp_path):
    driver = RGBCamera(overrides={"background_capture_on_init": False})
    captured = {}

    original_subplots = plt.subplots

    def capture_subplots(*args, **kwargs):
        figure, axes = original_subplots(*args, **kwargs)
        captured["axes"] = axes
        return figure, axes

    monkeypatch.setattr(plt, "subplots", capture_subplots)
    sample = {
        "cropped_img": np.zeros((2, 2, 3), dtype=np.uint8),
        "cx": 1,
        "cy": 1,
        "radius": 1,
        "row_crop": [1, 3],
        "col_crop": [2, 4],
    }

    driver.save_geometry_plot(
        np.zeros((5, 5, 3), dtype=np.uint8),
        sample,
        save_path=tmp_path,
        filename="geometry.png",
        show_full_image_axes=True,
    )

    assert captured["axes"][0].get_xlabel() == "px"
    assert captured["axes"][0].get_ylabel() == "py"
    assert captured["axes"][0].get_xticks().size > 0
    assert captured["axes"][0].get_yticks().size > 0
