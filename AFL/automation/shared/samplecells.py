"""Geometry and diagnostic plotting for shared sample-cell image pipelines."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from AFL.automation.vision.ImageProcessing import ImageProcessing


class NeutronSampleCell(ImageProcessing):
    """Describe the circular optical region of a neutron sample cell.

    This is deliberately not a :class:`Driver`.  Instrument drivers inherit
    it alongside ``Driver`` to share the cell's crop, circular ROI, and
    geometry plots while retaining their own camera acquisition and
    measurement logic.
    """

    geometry_defaults = {
        "row_crop": [0, 479],
        "col_crop": [0, 479],
        "hough_radii": 98,
    }

    def extract_sample_image(
        self,
        image,
        *,
        row_crop=None,
        col_crop=None,
        hough_radii=None,
        color_order="RGB",
    ):
        """Crop ``image`` to the cell and locate its circular sample region."""
        config = getattr(self, "config", {})
        row_crop = row_crop if row_crop is not None else config.get("row_crop")
        col_crop = col_crop if col_crop is not None else config.get("col_crop")
        hough_radii = (
            hough_radii if hough_radii is not None else config.get("hough_radii")
        )
        if hough_radii is None:
            raise ValueError("hough_radii must be configured for neutron sample-cell geometry")

        cropped_image = self.crop_image(image, row_crop=row_crop, col_crop=col_crop)
        circle = self.crop_to_circle(cropped_image, hough_radii=hough_radii)
        cx, cy = (int(value) for value in circle["center"])
        radius = int(circle["radius"])
        return {
            "cropped_img": cropped_image,
            "gray_img": self.to_grayscale(cropped_image, color_order=color_order),
            "mask": circle["mask"],
            "cx": cx,
            "cy": cy,
            "radius": radius,
            "row_crop": list(row_crop) if row_crop is not None else [0, image.shape[0]],
            "col_crop": list(col_crop) if col_crop is not None else [0, image.shape[1]],
        }

    @staticmethod
    def _display_image(image, color_order):
        image = np.asarray(image)
        if image.ndim == 3 and str(color_order).upper() == "BGR":
            return image[..., ::-1]
        return image

    def save_geometry_plot(
        self,
        raw_image,
        sample_image,
        *,
        save_path,
        filename,
        title="Detected neutron sample-cell region",
        color_order="RGB",
        overlay_mask=None,
    ):
        """Save a raw-frame crop and circular-ROI diagnostic plot."""
        import matplotlib.pyplot as plt
        from matplotlib.patches import Circle, Rectangle

        raw_image = np.asarray(raw_image)
        row_crop = sample_image["row_crop"]
        col_crop = sample_image["col_crop"]
        fig, axes = plt.subplots(1, 2, figsize=(12, 6))
        axes[0].imshow(self._display_image(raw_image, color_order))
        axes[0].add_patch(
            Rectangle(
                (col_crop[0], row_crop[0]),
                col_crop[1] - col_crop[0],
                row_crop[1] - row_crop[0],
                edgecolor="red",
                facecolor="none",
                linewidth=2,
            )
        )
        axes[0].set_title("Captured image with cell crop")
        axes[0].axis("off")

        axes[1].imshow(self._display_image(sample_image["cropped_img"], color_order))
        if overlay_mask is not None:
            axes[1].imshow(np.where(overlay_mask, 1.0, np.nan), alpha=0.35, cmap="magma")
        axes[1].add_patch(
            Circle(
                (sample_image["cx"], sample_image["cy"]),
                sample_image["radius"],
                edgecolor="red",
                facecolor="none",
                linewidth=2,
            )
        )
        axes[1].set_title(title)
        axes[1].axis("off")
        fig.tight_layout()

        output_path = Path(save_path) / filename
        output_path.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(output_path, dpi=100, bbox_inches="tight")
        plt.close(fig)
        return output_path

    def save_mask_comparison_plot(
        self,
        reference_image,
        measurement_image,
        mask,
        *,
        save_path,
        filename,
        title,
        invert_mask=False,
    ):
        """Save a shared reference/measurement mask diagnostic plot."""
        import matplotlib.pyplot as plt

        mask = ~np.asarray(mask, dtype=bool) if invert_mask else np.asarray(mask, dtype=bool)
        fig, axes = plt.subplots(1, 2)
        for axis, image, label in zip(
            axes, (reference_image, measurement_image), ("Reference", "Measurement")
        ):
            axis.imshow(image)
            axis.imshow(np.where(mask, 0.0, np.nan))
            axis.set_title(label)
            axis.axis("off")
        fig.suptitle(title)
        output_path = Path(save_path) / filename
        output_path.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(output_path)
        plt.close(fig)
        return output_path
