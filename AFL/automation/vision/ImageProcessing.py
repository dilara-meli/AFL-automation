"""Reusable image-processing helpers for camera drivers.

The :class:`ImageProcessing` mixin deliberately does not own a camera or a
``Driver`` lifecycle.  It can therefore be combined with any driver that
stores captured arrays in its dropbox (for example ``PiCameraDriver``).
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from AFL.automation.APIServer.Driver import Driver


class ImageProcessing:
    """Provide circular ROI, RGB, background, and turbidity processing.

    Images are expected to be two-dimensional greyscale arrays or three
    channel arrays.  The array methods are intentionally undecorated so they
    can also be used directly by a driver implementation.  The ``process_*``
    methods are lightweight, JSON-safe API operations for drivers which also
    inherit from :class:`~AFL.automation.APIServer.Driver.Driver`.
    """

    def _image_from_uid(self, image_uid):
        if self.dropbox is None or image_uid not in self.dropbox:
            raise KeyError(f"No image is available in the dropbox for image_uid={image_uid!r}")
        return np.asarray(self.dropbox[image_uid])

    @staticmethod
    def crop_image(image, row_crop=None, col_crop=None):
        """Return a rectangular crop of ``image`` after validating its bounds."""
        image = np.asarray(image)
        if image.ndim not in (2, 3):
            raise ValueError("image must be a 2-D greyscale or 3-D colour array")

        height, width = image.shape[:2]

        def validate_crop(crop, limit, name):
            if crop is None:
                return 0, limit
            if not isinstance(crop, Sequence) or isinstance(crop, (str, bytes)) or len(crop) != 2:
                raise ValueError(f"{name} must be a two-item [start, stop] sequence")
            start, stop = (int(value) for value in crop)
            if not 0 <= start < stop <= limit:
                raise ValueError(f"{name} must satisfy 0 <= start < stop <= {limit}")
            return start, stop

        row_start, row_stop = validate_crop(row_crop, height, "row_crop")
        col_start, col_stop = validate_crop(col_crop, width, "col_crop")
        return image[row_start:row_stop, col_start:col_stop].copy()

    @staticmethod
    def circular_mask(shape, center, radius):
        """Create a boolean mask for a circle within a two-dimensional shape."""
        if len(shape) < 2:
            raise ValueError("shape must have at least two dimensions")
        if not isinstance(center, Sequence) or isinstance(center, (str, bytes)) or len(center) != 2:
            raise ValueError("center must be a two-item [x, y] sequence")
        cx, cy = (float(value) for value in center)
        radius = float(radius)
        if radius <= 0:
            raise ValueError("radius must be positive")
        y, x = np.ogrid[: int(shape[0]), : int(shape[1])]
        return (x - cx) ** 2 + (y - cy) ** 2 < radius**2

    @staticmethod
    def find_circular_region(image, hough_radii):
        """Locate a circular ROI with a Hough transform.

        This optional operation imports scikit-image only when circle
        detection is requested, keeping ordinary Pi camera capture importable
        without the vision extra installed.
        """
        try:
            from skimage.color import rgb2gray
            from skimage.feature import canny
            from skimage.transform import hough_circle, hough_circle_peaks
            from skimage.util import img_as_ubyte
        except ImportError as exc:
            raise ImportError(
                "Circle detection requires scikit-image. Install AFL-automation[vision]."
            ) from exc

        image = np.asarray(image)
        if image.ndim == 3:
            gray = img_as_ubyte(rgb2gray(image))
        elif image.ndim == 2:
            gray = img_as_ubyte(image)
        else:
            raise ValueError("image must be a 2-D greyscale or 3-D colour array")
        radii = np.atleast_1d(hough_radii).astype(int)
        if radii.size == 0 or np.any(radii <= 0):
            raise ValueError("hough_radii must contain at least one positive radius")
        edges = canny(gray, sigma=2, low_threshold=10, high_threshold=50)
        _, cx, cy, detected_radii = hough_circle_peaks(
            hough_circle(edges, radii), radii, total_num_peaks=1
        )
        if not len(cx):
            raise RuntimeError(
                "Failed to locate a circular region. Supply center and radius or adjust hough_radii."
            )
        return int(cx[0]), int(cy[0]), int(detected_radii[0])

    def crop_to_circle(self, image, center=None, radius=None, hough_radii=None):
        """Return an image, circular mask, and circle metadata for a ROI.

        ``center`` and ``radius`` may be supplied explicitly.  Otherwise
        ``hough_radii`` is used to detect the circle.
        """
        image = np.asarray(image)
        if (center is None) != (radius is None):
            raise ValueError("center and radius must be provided together")
        if center is None:
            if hough_radii is None:
                raise ValueError("provide center and radius, or hough_radii for circle detection")
            cx, cy, radius = self.find_circular_region(image, hough_radii)
        else:
            cx, cy = (float(value) for value in center)
        mask = self.circular_mask(image.shape, (cx, cy), radius)
        return {
            "image": image.copy(),
            "mask": mask,
            "center": (float(cx), float(cy)),
            "radius": float(radius),
        }

    @staticmethod
    def to_grayscale(image, color_order="RGB"):
        """Convert a colour image to greyscale, respecting RGB or BGR order."""
        image = np.asarray(image)
        if image.ndim == 2:
            return image.astype(float, copy=False)
        if image.ndim != 3 or image.shape[2] < 3:
            raise ValueError("colour image must have at least three channels")
        color_order = str(color_order).upper()
        if color_order == "RGB":
            red, green, blue = image[..., 0], image[..., 1], image[..., 2]
        elif color_order == "BGR":
            blue, green, red = image[..., 0], image[..., 1], image[..., 2]
        else:
            raise ValueError("color_order must be 'RGB' or 'BGR'")
        return 0.2125 * red + 0.7154 * green + 0.0721 * blue

    def rgb_values(self, image, mask=None, color_order="RGB"):
        """Calculate mean red, green, and blue values, optionally inside ``mask``."""
        image = np.asarray(image)
        if image.ndim != 3 or image.shape[2] < 3:
            raise ValueError("RGB values require an image with at least three channels")
        if mask is None:
            mask = np.ones(image.shape[:2], dtype=bool)
        mask = np.asarray(mask, dtype=bool)
        if mask.shape != image.shape[:2]:
            raise ValueError("mask shape must match the first two image dimensions")
        if not mask.any():
            raise ValueError("mask does not contain any pixels")
        color_order = str(color_order).upper()
        if color_order == "RGB":
            red, green, blue = image[..., 0], image[..., 1], image[..., 2]
        elif color_order == "BGR":
            blue, green, red = image[..., 0], image[..., 1], image[..., 2]
        else:
            raise ValueError("color_order must be 'RGB' or 'BGR'")
        return {"R": float(np.mean(red[mask])), "G": float(np.mean(green[mask])), "B": float(np.mean(blue[mask]))}

    def prepare_background(self, image, row_crop=None, col_crop=None, color_order="RGB"):
        """Crop and convert a background image into greyscale intensity data."""
        return self.to_grayscale(self.crop_image(image, row_crop, col_crop), color_order)

    def turbidity_measurement(self, image, background, mask=None, color_order="RGB"):
        """Calculate normalized transmission relative to a background image.

        The returned ``turbidity_metric`` matches the legacy optical-turbidity
        convention: mean sample intensity divided by background intensity.
        Lower values therefore indicate a less transmitting (more turbid)
        sample.
        """
        measurement = self.to_grayscale(image, color_order)
        background = self.to_grayscale(background, color_order)
        if measurement.shape != background.shape:
            raise ValueError("image and background must have identical dimensions")
        if mask is None:
            mask = np.ones(measurement.shape, dtype=bool)
        mask = np.asarray(mask, dtype=bool)
        if mask.shape != measurement.shape or not mask.any():
            raise ValueError("mask must match the image dimensions and contain pixels")
        empty_intensity = background[mask]
        filled_intensity = measurement[mask]
        pedestal = abs(float(np.min(empty_intensity))) + 1.0
        normalized = (filled_intensity + pedestal) / (empty_intensity + pedestal)
        normalized = np.nan_to_num(normalized, nan=1.0, posinf=1.0, neginf=1.0)
        return {
            "turbidity_metric": float(np.mean(normalized)),
            "normalized_intensity": normalized,
        }

    @Driver.queued()
    def set_background(self, image_uid):
        """Set a retained background image from a previously captured image UID."""
        self._background_image = self._image_from_uid(image_uid).copy()
        return {"background_set": True, "shape": [int(value) for value in self._background_image.shape]}

    def _crop_measurement_image(self, image, row_crop=None, col_crop=None):
        """Crop an image only when both rectangular ROI bounds are supplied."""
        if row_crop is None or col_crop is None:
            return np.asarray(image)
        return self.crop_image(image, row_crop=row_crop, col_crop=col_crop)

    @Driver.unqueued()
    def measure_mean_rgb(
        self,
        image_uid,
        center=None,
        radius=None,
        hough_radii=None,
        color_order="RGB",
        row_crop=None,
        col_crop=None,
    ):
        """Return RGB means for a captured image, optionally inside a rectangular and circular ROI.

        A rectangular ROI is applied only when both ``row_crop`` and
        ``col_crop`` are provided. Circle coordinates and Hough detection are
        relative to that rectangular crop.
        """
        image = self._crop_measurement_image(
            self._image_from_uid(image_uid), row_crop=row_crop, col_crop=col_crop
        )
        if center is None and radius is None and hough_radii is None:
            mask = None
            circle = None
        else:
            circle = self.crop_to_circle(image, center, radius, hough_radii)
            mask = circle["mask"]
        result = {"avg_rgb": self.rgb_values(image, mask, color_order)}
        if circle is not None:
            result["center"] = list(circle["center"])
            result["radius"] = circle["radius"]
        return result

    @Driver.unqueued()
    def measure_turbidity(
        self,
        image_uid,
        background_uid=None,
        center=None,
        radius=None,
        hough_radii=None,
        color_order="RGB",
        row_crop=None,
        col_crop=None,
    ):
        """Return turbidity, optionally inside a rectangular and circular ROI.

        A rectangular ROI is applied only when both ``row_crop`` and
        ``col_crop`` are provided. The identical crop is applied to the image
        and its background before circle detection and normalization.
        """
        image = self._image_from_uid(image_uid)
        if background_uid is not None:
            background = self._image_from_uid(background_uid)
        elif getattr(self, "_background_image", None) is not None:
            background = self._background_image
        else:
            raise ValueError("set a background image or provide background_uid before measuring turbidity")
        image = self._crop_measurement_image(image, row_crop=row_crop, col_crop=col_crop)
        background = self._crop_measurement_image(
            background, row_crop=row_crop, col_crop=col_crop
        )
        if center is None and radius is None and hough_radii is None:
            mask = None
            circle = None
        else:
            circle = self.crop_to_circle(background, center, radius, hough_radii)
            mask = circle["mask"]
        result = self.turbidity_measurement(image, background, mask, color_order)
        response = {"turbidity_metric": result["turbidity_metric"]}
        if circle is not None:
            response["center"] = list(circle["center"])
            response["radius"] = circle["radius"]
        return response
