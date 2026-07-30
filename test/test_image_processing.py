import numpy as np
import pytest

from AFL.automation.vision.ImageProcessing import ImageProcessing
from AFL.automation.vision.PiCameraDriver import PiCameraDriver


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
