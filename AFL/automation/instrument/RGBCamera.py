import datetime
import pathlib
import time

import lazy_loader as lazy
import numpy as np
import xarray as xr
from skimage.color import rgb2gray
from skimage.feature import canny
from skimage.transform import hough_circle, hough_circle_peaks
from skimage.util import img_as_ubyte

from AFL.automation.APIServer.Driver import Driver

cv2 = lazy.load("cv2", require="AFL-automation[vision]")


class RGBCamera(Driver):
    """
    Driver for capturing RGB images and computing average RGB values.
    
    This driver interfaces with a USB camera to capture images and extract
    the average RGB values along with image metadata.
    """
    
    defaults = {}
    defaults["camera_interface"] = "usb"
    defaults["camera_index"] = 0
    defaults["save_path"] = "/home/afl642/rgb_images/"
    defaults["row_crop"] = [0, 479]
    defaults["col_crop"] = [0, 479]
    defaults["hough_radii"] = 98

    def __init__(self, camera=None, overrides=None):
        """
        Initialize RGBCamera driver.

        Parameters
        ----------
        camera : object, optional
            Camera object (e.g., USBCamera instance). If None, one will be
            created when using the USB interface.
        overrides : dict, optional
            Configuration overrides for PersistentConfig.
        """
        self.camera = camera
        self._opencv_capture = None
        Driver.__init__(
            self,
            name="RGBCamera",
            defaults=self.gather_defaults(),
            overrides=overrides,
        )

        if self.camera is None and self.config["camera_interface"] == "usb":
            from AFL.automation.instrument.USBCamera import USBCamera

            camera_index = self.config.get("camera_index", 0)
            self.camera = USBCamera(camid=camera_index)

    def _collect_image(self, **kwargs):
        """
        Collect an image based on the configured camera interface.

        Returns
        -------
        tuple
            `(collected, img)` where `collected` indicates success.
        """
        interface = self.config["camera_interface"]

        if interface == "usb":
            if self.camera is None:
                from AFL.automation.instrument.USBCamera import USBCamera

                camera_index = self.config.get("camera_index", 0)
                self.camera = USBCamera(camid=camera_index)
            return self.camera.collect(**kwargs)

        if interface == "opencv":
            try:
                cv2_module = lazy.load("cv2", require="AFL-automation[vision]")
            except Exception as exc:
                raise ImportError(
                    "opencv-python is required for camera_interface='opencv'. "
                    f"Install with: pip install AFL-automation[vision]. Error: {exc}"
                )

            if "camera_index" not in self.config:
                raise ValueError("camera_index must be set in config when camera_interface='opencv'")

            camera_index = self.config["camera_index"]
            if self._opencv_capture is None:
                self._opencv_capture = cv2_module.VideoCapture(camera_index)

            return self._opencv_capture.read()

        raise ValueError(
            f"Unsupported camera_interface: '{interface}'. "
            "Supported values are: 'usb', 'opencv'"
        )

    def _reset_camera(self):
        """Reset the configured camera connection."""
        interface = self.config["camera_interface"]

        if interface == "usb":
            if self.camera is not None:
                self.camera.camera_reset()
            return

        if interface == "opencv":
            if self._opencv_capture is not None:
                self._opencv_capture.release()
            try:
                cv2_module = lazy.load("cv2", require="AFL-automation[vision]")
            except Exception as exc:
                raise ImportError(
                    "opencv-python is required for camera_interface='opencv'. "
                    f"Install with: pip install AFL-automation[vision]. Error: {exc}"
                )
            camera_index = self.config.get("camera_index", 0)
            self._opencv_capture = cv2_module.VideoCapture(camera_index)

    def _process_image(self, img, row_crop=None, col_crop=None, hough_radii=None):
        """
        Crop the image, locate the circular sample region, and compute masked RGB averages.

        Parameters
        ----------
        img : np.ndarray
            Input image in BGR format (from OpenCV).
        row_crop : list, optional
            Row range [start, end] for cropping. If None, uses full image.
        col_crop : list, optional
            Column range [start, end] for cropping. If None, uses full image.
        hough_radii : int or list, optional
            Radius or radii to use for Hough circle detection.

        Returns
        -------
        dict
            Processed image payload including cropped image, mask, center, radius,
            and average RGB values computed inside the mask.
        """
        if row_crop is None:
            row_crop = [0, img.shape[0]]
        if col_crop is None:
            col_crop = [0, img.shape[1]]
        if hough_radii is None:
            hough_radii = self.config.get("hough_radii", 98)

        cropped_img = img[row_crop[0] : row_crop[1], col_crop[0] : col_crop[1], :]
        gray_img = img_as_ubyte(rgb2gray(cropped_img))

        radii = list(np.atleast_1d(hough_radii))
        edges = canny(gray_img, sigma=2, low_threshold=10, high_threshold=50)
        hough_res = hough_circle(edges, radii)
        _, cx, cy, detected_radii = hough_circle_peaks(hough_res, radii, total_num_peaks=1)

        if len(cx) == 0 or len(cy) == 0 or len(detected_radii) == 0:
            raise RuntimeError(
                "Failed to locate sample region with Hough circle detection. "
                "Adjust row_crop, col_crop, or hough_radii."
            )

        cx = int(cx[0])
        cy = int(cy[0])
        detected_radius = int(detected_radii[0])

        y = np.arange(cropped_img.shape[0])
        x = np.arange(cropped_img.shape[1])
        X, Y = np.meshgrid(x, y)
        mask = np.sqrt((X - cx) * (X - cx) + (Y - cy) * (Y - cy)) < detected_radius

        avg_blue = np.mean(cropped_img[:, :, 0][mask])
        avg_green = np.mean(cropped_img[:, :, 1][mask])
        avg_red = np.mean(cropped_img[:, :, 2][mask])

        return {
            "cropped_img": cropped_img,
            "gray_img": gray_img,
            "mask": mask,
            "cx": cx,
            "cy": cy,
            "radius": detected_radius,
            "avg_rgb": {
                "R": float(avg_red),
                "G": float(avg_green),
                "B": float(avg_blue),
            },
        }

    def _build_dataset(
        self,
        *,
        name,
        avg_rgb,
        measurement_img,
        mask,
        cx,
        cy,
        radius,
        img_metadata,
    ):
        """
        Build an xarray Dataset containing RGB measurements, mask, and metadata.
        """
        ds = xr.Dataset()
        ds.attrs["name"] = name
        ds.attrs["avg_R"] = avg_rgb["R"]
        ds.attrs["avg_G"] = avg_rgb["G"]
        ds.attrs["avg_B"] = avg_rgb["B"]
        ds.attrs["timestamp"] = img_metadata["timestamp"]
        ds.attrs["image_height"] = img_metadata["height"]
        ds.attrs["image_width"] = img_metadata["width"]
        ds.attrs["camera_index"] = self.config.get("camera_index", 0)
        ds.attrs["located_center"] = [cx, cy]
        ds.attrs["mask_radius"] = radius

        ds["avg_rgb"] = xr.DataArray(
            [avg_rgb["R"], avg_rgb["G"], avg_rgb["B"]],
            coords={"channel": ["R", "G", "B"]},
        )
        ds["img_bgr"] = (("height", "width", "channel"), measurement_img)
        ds["mask"] = (("height", "width"), mask)

        return ds

    @Driver.queued(
        qb={
            "button_text": "Capture RGB",
            "params": {
                "name": {"label": "Measurement Name", "type": "text", "default": ""},
                "plotting": {"label": "Save diagnostic plots", "type": "bool", "default": False},
            },
        }
    )
    def capture_rgb(self, name="", plotting=False, **kwargs):
        """
        Capture an image and compute average RGB values.

        Parameters
        ----------
        name : str, optional
            Name/identifier for the measurement.
        plotting : bool, optional
            If True, save diagnostic plots of the captured image.
        **kwargs : dict
            Additional arguments passed to image collection.

        Returns
        -------
        xarray.Dataset
            Dataset with average RGB values, image, and metadata.
        """
        row_crop = self.config.get("row_crop", [0, 479])
        col_crop = self.config.get("col_crop", [0, 479])
        hough_radii = self.config.get("hough_radii", 98)

        print(f"Capturing RGB image (crops: rows {row_crop}, cols {col_crop})")
        print(f"Using hough_radii={hough_radii}")

        print("Attempting to collect camera image")
        self._reset_camera()
        time.sleep(0.2)
        collected, img = self._collect_image(**kwargs)

        if collected:
            print("Successfully collected image")
        else:
            self._reset_camera()
            print("Attempting to reset camera connection and retry")
            time.sleep(0.2)
            collected, img = self._collect_image(**kwargs)
            if collected:
                print("Success on retry")
            else:
                raise RuntimeError(
                    "Failed to collect camera image after two attempts. "
                    "Check that the camera is connected and the "
                    f"camera_interface ('{self.config['camera_interface']}') "
                    "settings are correct."
                )

        processed = self._process_image(
            img,
            row_crop=row_crop,
            col_crop=col_crop,
            hough_radii=hough_radii,
        )
        avg_rgb = processed["avg_rgb"]
        print(f"Average RGB: R={avg_rgb['R']:.2f}, G={avg_rgb['G']:.2f}, B={avg_rgb['B']:.2f}")

        img_metadata = {
            "timestamp": datetime.datetime.now().isoformat(),
            "height": processed["cropped_img"].shape[0],
            "width": processed["cropped_img"].shape[1],
        }

        if plotting:
            try:
                import matplotlib.pyplot as plt

                fig, ax = plt.subplots(1, 2, figsize=(12, 6))
                img_rgb = processed["cropped_img"][:, :, ::-1]
                ax[0].imshow(img_rgb)
                ax[0].set_title("Captured Image")
                ax[0].axis("off")

                ax[1].imshow(img_rgb)
                ax[1].imshow(np.where(processed["mask"], 0.0, np.nan), alpha=0.35)
                ax[1].set_title(
                    f"Masked RGB: R={avg_rgb['R']:.1f}, G={avg_rgb['G']:.1f}, B={avg_rgb['B']:.1f}"
                )
                ax[1].axis("off")

                save_path = pathlib.Path(self.config.get("save_path", "./"))
                save_path.mkdir(parents=True, exist_ok=True)
                plot_file = (
                    save_path
                    / f"{datetime.datetime.now().strftime('%Y-%m-%d-%H-%M-%S')}-rgb-capture.png"
                )
                plt.savefig(plot_file, dpi=100, bbox_inches="tight")
                plt.close(fig)
                print(f"Saved plot to {plot_file}")
            except Exception as e:
                print(f"Warning: Could not save plot: {e}")

        ds = self._build_dataset(
            name=name,
            avg_rgb=avg_rgb,
            measurement_img=processed["cropped_img"],
            mask=processed["mask"],
            cx=processed["cx"],
            cy=processed["cy"],
            radius=processed["radius"],
            img_metadata=img_metadata,
        )

        return ds


_DEFAULT_CUSTOM_CONFIG = {
    "_classname": "AFL.automation.instrument.RGBCamera.RGBCamera",
    "_args": [
        {
            "_classname": "AFL.automation.instrument.USBCamera.USBCamera",
            "_args": [0],
        }
    ],
}
_DEFAULT_CUSTOM_PORT = 5002

if __name__ == "__main__":
    from AFL.automation.shared.launcher import *
