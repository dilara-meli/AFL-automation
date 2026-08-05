"""Vision drivers and reusable image-processing helpers."""

__all__ = ["ImageProcessing", "PiCameraDriver", "OT2RidingCam", "RGBCamera"]


def __getattr__(name):
    """Load drivers on demand so ``python -m`` can execute them cleanly."""
    if name == "PiCameraDriver":
        from .PiCameraDriver import PiCameraDriver

        return PiCameraDriver
    if name == "ImageProcessing":
        from .ImageProcessing import ImageProcessing

        return ImageProcessing
    if name == "OT2RidingCam":
        from .OT2RidingCam import OT2RidingCam

        return OT2RidingCam
    if name == "RGBCamera":
        from .RGBCamera import RGBCamera

        return RGBCamera
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
