"""A Raspberry Pi camera driver mounted on an OT-2 gantry."""

from __future__ import annotations

from AFL.automation.APIServer.Driver import Driver
from AFL.automation.manipulate.OT2GantryDriver import OT2GantryDriver
from AFL.automation.vision.PiCameraDriver import PiCameraDriver


class OT2RidingCam(PiCameraDriver, OT2GantryDriver):
    """Combine the complete Pi camera and OT-2 gantry driver interfaces.

    The class directly inherits its public commands: camera capture, streaming,
    RGB and turbidity measurement come from :class:`PiCameraDriver`; well and
    relative movement come from :class:`OT2GantryDriver`.  The gantry still
    uses its OT2Prepare proxy connection as designed.
    """

    def __init__(
        self,
        camera=None,
        overrides=None,
        ot2_prepare_ip=None,
        ot2_prepare_port=None,
    ):
        """Initialize the local camera and inherited OT2 gantry proxy.

        ``ot2_prepare_ip`` and ``ot2_prepare_port`` are forwarded to
        :class:`OT2GantryDriver` and take precedence over ``overrides``.
        """
        # Initialize the shared Driver once, then invoke each parent
        # initializer in state-only mode.  This follows normal multiple-driver
        # composition while avoiding a second PersistentConfig instance.
        Driver.__init__(
            self,
            name="OT2RidingCam",
            defaults=self.gather_defaults(),
            overrides=overrides,
        )
        PiCameraDriver.__init__(
            self,
            camera=camera,
            overrides=overrides,
            name="OT2RidingCam",
            initialize_driver=False,
        )
        OT2GantryDriver.__init__(
            self,
            overrides=overrides,
            ot2_prepare_ip=ot2_prepare_ip,
            ot2_prepare_port=ot2_prepare_port,
            initialize_driver=False,
        )


_DEFAULT_CUSTOM_CONFIG = {
    "_classname": "AFL.automation.vision.OT2RidingCam.OT2RidingCam",
    "overrides": {
        "ot2_prepare_ip": "127.0.0.1", # should be the network switch where you start the AFL servers 
        "ot2_prepare_port": "5005",
        "resolution": [1920, 1080],
    },
}
_DEFAULT_PORT = 5007

if __name__ == "__main__":
    from AFL.automation.shared.launcher import *
