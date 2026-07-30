"""Client-side gantry proxy for an OT2Prepare APIServer."""

import math
import re

from AFL.automation.APIServer.Client import Client
from AFL.automation.APIServer.Driver import Driver, ProxyDriver


class OT2GantryDriver(ProxyDriver):
    """Translate gantry requests into OT2Prepare atomic-command queue tasks.

    Configure this driver with the APIServer address hosting ``OT2Prepare``.
    It never contacts the robot IP; every physical command is enqueued on the
    owner server as an ``_execute_atomic_command`` task.
    """

    defaults = {
        # Address of the APIServer running OT2Prepare, not the robot itself.
        "ot2_prepare_ip": "127.0.0.1",
        "ot2_prepare_port": "5005",
        # OT-2's owner API requires a pipette ID to resolve a well position.
        # This is only an internal coordinate reference; it is not exposed by
        # the gantry API.
        "gantry_reference_mount": "left",
        # Parked pipette heights, relative to the selected well's origin.
        # Set these to the calibrated Z offsets that keep each pipette clear
        # while the shared XY gantry travels between wells.
        "left_pipette_zero": 0.0,
        "right_pipette_zero": 0.0,
        # Offset of the payload carried by the gantry from the centre of the
        # reference pipette, in millimetres.  This lets a riding camera (or
        # another fixed payload) target a well without changing every call.
        "offset_x": 0.0,
        "offset_y": 0.0,
        "offset_z": 0.0,
    }

    _WELL_NAME_RE = re.compile(r"^[A-Za-z]+[0-9]+$")
    _WELL_ORIGINS = {"top", "bottom", "center"}

    def __init__(
        self,
        overrides=None,
        ot2_prepare_ip=None,
        ot2_prepare_port=None,
        initialize_driver=True,
    ):
        """Initialize the gantry proxy and its OT2Prepare target settings."""
        overrides = dict(overrides or {})
        if ot2_prepare_ip is not None:
            overrides["ot2_prepare_ip"] = ot2_prepare_ip
        if ot2_prepare_port is not None:
            overrides["ot2_prepare_port"] = str(ot2_prepare_port)
        if initialize_driver:
            super().__init__(
                name="OT2_Gantry_Driver",
                defaults=self.gather_defaults(),
                overrides=overrides,
            )
        self._initialize_gantry_state(
            ot2_prepare_ip=ot2_prepare_ip,
            ot2_prepare_port=ot2_prepare_port,
        )

    def _initialize_gantry_state(self, ot2_prepare_ip=None, ot2_prepare_port=None):
        """Initialize local proxy state without creating a second Driver."""
        # A composed driver may have initialized Driver through a sibling
        # class rather than ProxyDriver.__init__.
        if not hasattr(self, "_proxy_clients"):
            self._proxy_clients = {}
        if ot2_prepare_ip is not None:
            self.config["ot2_prepare_ip"] = ot2_prepare_ip
        if ot2_prepare_port is not None:
            self.config["ot2_prepare_port"] = str(ot2_prepare_port)
        self._ot2_prepare_client = None
        self._pipette_locations = {}
        self._gantry_location = None

    @Driver.queued()
    def move_to_well(
        self,
        location,
        origin="top",
        offset_x=None,
        offset_y=None,
        offset_z=None,
    ):
        """Queue a gantry move to a loaded OT2Prepare well.

        Unspecified offsets use the driver-level payload offsets.  Supplying
        an individual offset overrides only that axis for this move.  Pipette
        mounts and their Z positions are deliberately not part of this API.
        """
        slot, well = self._parse_location(location)
        origin = self._normalize_origin(origin)
        payload_offset = self._offset(
            self.config["offset_x"] if offset_x is None else offset_x,
            self.config["offset_y"] if offset_y is None else offset_y,
            self.config["offset_z"] if offset_z is None else offset_z,
        )
        reference_mount = self._reference_mount()
        offset = dict(payload_offset)
        offset["z"] += self._pipette_zero(reference_mount)
        owner_config = self._owner_config()
        target = self._resolve_target(
            owner_config,
            slot,
            well,
            reference_mount,
        )
        result = self._queued_result([self._enqueue_atomic_move(target, origin, offset)])
        # A well-relative command gives us the coordinate origin needed for
        # subsequent relative moves.  Movement is shared in XY, while each
        # pipette has its own Z position.
        self._pipette_locations = {
            reference_mount: offset["z"],
        }
        self._gantry_location = {
            "location": f"{slot}{well}",
            "origin": origin,
            "x": offset["x"],
            "y": offset["y"],
            "payload_z": payload_offset["z"],
        }
        return result

    @Driver.queued()
    def move_pipette(self, mount="", dx=0.0, dy=0.0, dz=0.0):
        """Move a loaded pipette by a well-relative displacement in millimetres.

        Call :meth:`move_to_well` first to establish the well-relative origin.
        An empty ``mount`` uses ``gantry_reference_mount``.  The OT-2 owner
        API has no relative-motion command, so the driver accumulates the
        requested displacement and submits the resulting ``moveToWell``
        command for the selected pipette.
        """
        mount = self._reference_mount() if not str(mount).strip() else self._normalize_mount(mount)
        delta = self._offset(dx, dy, dz)
        if self._gantry_location is None:
            raise ValueError("No previous gantry target. Call move_to_well first.")
        previous_z = self._pipette_locations.get(
            mount,
            self._gantry_location["payload_z"] + self._pipette_zero(mount),
        )
        offset = {
            "x": self._gantry_location["x"] + delta["x"],
            "y": self._gantry_location["y"] + delta["y"],
            "z": previous_z + delta["z"],
        }
        slot, well = self._parse_location(self._gantry_location["location"])
        target = self._resolve_target(self._owner_config(), slot, well, mount)
        result = self._queued_result(
            [self._enqueue_atomic_move(target, self._gantry_location["origin"], offset)]
        )
        self._pipette_locations[mount] = offset["z"]
        self._gantry_location.update(x=offset["x"], y=offset["y"])
        return result

    def _owner_config(self):
        result = self._get_ot2_prepare_client().get_config(
            "all", print_console=False, interactive=True
        )
        if not isinstance(result, dict) or result.get("exit_state") != "Success!":
            raise RuntimeError(
                "Unable to read OT2Prepare configuration before queuing gantry motion"
            )
        config = result.get("return_val")
        if not isinstance(config, dict):
            raise RuntimeError("OT2Prepare returned an invalid configuration payload")
        return config

    def _resolve_target(self, config, slot, well, mount):
        try:
            labware = config["loaded_labware"][slot]
            pipette = config["loaded_instruments"][mount]
            labware_id = labware[0]
            pipette_id = pipette["pipette_id"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError(
                f"OT2Prepare has no loaded labware in slot {slot!r} or no pipette on "
                f"the configured gantry reference mount {mount!r}"
            ) from exc
        if not pipette_id:
            raise ValueError(f"OT2Prepare has no active pipette ID on {mount!r} mount")
        return {
            "pipette_id": pipette_id,
            "labware_id": labware_id,
            "well": well,
        }

    def _enqueue_atomic_move(self, target, origin, offset):
        atomic_params = {
            "pipetteId": target["pipette_id"],
            "labwareId": target["labware_id"],
            "wellName": target["well"],
            "wellLocation": {"origin": origin, "offset": offset},
        }
        return self._get_ot2_prepare_client().enqueue(
            task_name="_execute_atomic_command",
            command_type="moveToWell",
            # Client.enqueue reserves ``params`` for a local callable whose
            # result is merged into the queued task payload.
            params=lambda: {"params": atomic_params},
            interactive=False,
        )

    def _queued_result(self, task_uuids):
        client = self._get_ot2_prepare_client()
        return {
            "owner_task_uuids": [str(task_uuid) for task_uuid in task_uuids],
            "owner_server": getattr(client, "url", None),
        }

    def _get_ot2_prepare_client(self):
        if self._ot2_prepare_client is None:
            ip = self.config.get("ot2_prepare_ip")
            if not ip:
                raise ValueError(
                    "ip must name the APIServer running OT2Prepare"
                )
            self._ot2_prepare_client = self.get_proxy_client(
                "ot2_prepare",
                ip=ip,
                port=str(self.config["ot2_prepare_port"]),
                username="OT2GantryDriver",
                # Client logs in during construction and retains the JWT in
                # its Authorization header for subsequent owner requests.
                client_factory=Client,
            )
        return self._ot2_prepare_client

    def _parse_location(self, location):
        if not isinstance(location, str):
            raise ValueError("location must be a string such as '1A1'")
        match = re.fullmatch(r"([0-9]+)([A-Za-z]+[0-9]+)", location.strip())
        if not match or not self._WELL_NAME_RE.fullmatch(match.group(2)):
            raise ValueError("location must use the form '<deck slot><well>', such as '1A1'")
        return match.group(1), match.group(2).upper()

    @staticmethod
    def _normalize_mount(mount):
        mount = str(mount).strip().lower()
        if mount not in {"left", "right"}:
            raise ValueError("mount must be 'left' or 'right'")
        return mount

    def _reference_mount(self):
        return self._normalize_mount(self.config["gantry_reference_mount"])

    def _pipette_zero(self, mount):
        """Return the configured parked Z offset for ``mount`` in millimetres."""
        return self._offset(0.0, 0.0, self.config[f"{mount}_pipette_zero"])["z"]

    def _normalize_origin(self, origin):
        origin = str(origin).strip().lower()
        if origin not in self._WELL_ORIGINS:
            raise ValueError("origin must be 'top', 'bottom', or 'center'")
        return origin

    @staticmethod
    def _offset(x, y, z):
        try:
            offset = {"x": float(x), "y": float(y), "z": float(z)}
        except (TypeError, ValueError) as exc:
            raise ValueError("offsets must be finite numbers in millimeters") from exc
        if not all(math.isfinite(value) for value in offset.values()):
            raise ValueError("offsets must be finite numbers in millimeters")
        return offset

_DEFAULT_PORT = 5006
if __name__ == "__main__":
    from AFL.automation.shared.launcher import *
