"""Vision drivers and camera integrations."""

__all__ = ["PiCameraDriver"]


def __getattr__(name):
    """Load drivers on demand so ``python -m`` can execute them cleanly."""
    if name == "PiCameraDriver":
        from .PiCameraDriver import PiCameraDriver

        return PiCameraDriver
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
