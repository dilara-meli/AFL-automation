import numpy as np
import pytest

from AFL.automation.APIServer.Driver import Driver
from AFL.automation.shared.samplecells import NeutronSampleCell
from AFL.automation.vision.ImageProcessing import ImageProcessing
from AFL.automation.vision.PiCameraDriver import PiCameraDriver
from AFL.automation.vision.RGBCamera import RGBCamera, _DEFAULT_CUSTOM_CONFIG


class FakeCamera:
    def capture_array(self, stream_name):
        return np.zeros((2, 3, 3), dtype=np.uint8)


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
    driver = RGBCamera(overrides={"background_capture_on_init": False})
    image = np.zeros((6, 6, 3), dtype=np.uint8)
    image[..., 0] = 30  # B
    image[..., 1] = 20  # G
    image[..., 2] = 10  # R

    monkeypatch.setattr(driver, "find_circular_region", lambda image, radii: (3, 3, 2))
    processed = driver._process_image(image, px_crop=[1, 5], py_crop=[1, 5], hough_radii=2)

    assert isinstance(driver, (NeutronSampleCell, ImageProcessing, Driver))
    assert processed["avg_rgb"] == {"R": 10.0, "G": 20.0, "B": 30.0}
    assert processed["mask"].shape == (4, 4)
    assert "measure_mean_rgb" in driver.unqueued.functions
    assert "set_background" in driver.queued.functions
    assert RGBCamera.__module__ == "AFL.automation.vision.RGBCamera"
    assert _DEFAULT_CUSTOM_CONFIG["_classname"] == "AFL.automation.vision.RGBCamera.RGBCamera"


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
