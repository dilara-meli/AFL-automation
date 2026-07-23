"""Client-side gantry proxy for an OT2Prepare APIServer."""

import math
import re

from AFL.automation.APIServer.Client import Client
from AFL.automation.APIServer.Driver import Driver


class OT2GantryDriver(Driver):
    """Translate gantry requests into OT2Prepare atomic-command queue tasks.

    Configure this driver with the APIServer address hosting ``OT2Prepare``.
    It never contacts the robot IP; every physical command is enqueued on the
    owner server as an ``_execute_atomic_command`` task.
    """

    defaults = {
        # Address of the APIServer running OT2Prepare, not the robot itself.
        "ip": "127.0.0.1",
        "port": "5005",
        "gantry_relative_move_mm": 1.0,
        "gantry_safe_clearance_mm": 50.0,
    }

    _WELL_NAME_RE = re.compile(r"^[A-Za-z]+[0-9]+$")
    _WELL_ORIGINS = {"top", "bottom", "center"}

    def __init__(self, overrides=None):
        super().__init__(
            name="OT2_Gantry_Driver",
            defaults=self.gather_defaults(),
            overrides=overrides,
        )
        self._ot2_prepare_client = None
        self._gantry_locations = {}

    @Driver.queued()
    def move_to_well(
        self,
        location,
        mount="left",
        origin="top",
        offset_x=0.0,
        offset_y=0.0,
        offset_z=0.0,
    ):
        """Queue a safe approach and move to a loaded OT2Prepare well."""
        mount = self._normalize_mount(mount)
        slot, well = self._parse_location(location)
        origin = self._normalize_origin(origin)
        offset = self._offset(offset_x, offset_y, offset_z)
        owner_config = self._owner_config()
        target = self._resolve_target(owner_config, slot, well, mount)

        safe_offset = self._safe_offset(owner_config, target["labware_id"], offset)
        task_uuids = [
            self._enqueue_atomic_move(target, origin="top", offset=safe_offset),
            self._enqueue_atomic_move(target, origin=origin, offset=offset),
        ]
        self._gantry_locations[mount] = {
            "location": f"{slot}{well}",
            "origin": origin,
            "offset": offset,
        }
        return self._queued_result(task_uuids)

    @Driver.queued()
    def move_pipette_up(self, mount="left", distance=None):
        """Queue a well-relative upward move on OT2Prepare."""
        return self._move_relative_z(mount, distance, direction=1)

    @Driver.queued()
    def move_pipette_down(self, mount="left", distance=None):
        """Queue a well-relative downward move on OT2Prepare."""
        return self._move_relative_z(mount, distance, direction=-1)

    def _move_relative_z(self, mount, distance, direction):
        mount = self._normalize_mount(mount)
        if distance is None:
            distance = self.config["gantry_relative_move_mm"]
        distance = self._positive_distance(distance)
        try:
            previous = self._gantry_locations[mount]
        except KeyError as exc:
            raise ValueError(
                f"No previous gantry target for {mount} mount. Call move_to_well first."
            ) from exc

        slot, well = self._parse_location(previous["location"])
        owner_config = self._owner_config()
        target = self._resolve_target(owner_config, slot, well, mount)
        offset = dict(previous["offset"])
        offset["z"] += direction * distance
        task_uuid = self._enqueue_atomic_move(target, previous["origin"], offset)
        self._gantry_locations[mount]["offset"] = offset
        return self._queued_result([task_uuid])

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
                f"the {mount!r} mount"
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
            ip = self.config.get("ip")
            if not ip:
                raise ValueError(
                    "ip must name the APIServer running OT2Prepare"
                )
            self._ot2_prepare_client = Client(
                ip=ip,
                port=str(self.config["port"]),
                # Client logs in during construction and retains the JWT in
                # its Authorization header for subsequent owner requests.
                username="OT2GantryDriver",
            )
        return self._ot2_prepare_client

    def _safe_offset(self, config, target_labware_id, requested_offset):
        target_height = self._labware_height(config, target_labware_id)
        tallest_height = max(
            (self._labware_height(config, labware[0]) for labware in config.get("loaded_labware", {}).values()),
            default=target_height,
        )
        safe_offset = dict(requested_offset)
        safe_offset["z"] = max(
            requested_offset["z"], tallest_height - target_height
        ) + self._positive_distance(self.config["gantry_safe_clearance_mm"])
        return safe_offset

    @staticmethod
    def _labware_height(config, labware_id):
        for labware in config.get("loaded_labware", {}).values():
            if not isinstance(labware, (list, tuple)) or len(labware) < 3 or labware[0] != labware_id:
                continue
            try:
                height = float(labware[2]["definition"]["dimensions"]["z"])
            except (KeyError, TypeError, ValueError):
                return 0.0
            return height if math.isfinite(height) and height >= 0 else 0.0
        return 0.0

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

    @staticmethod
    def _positive_distance(distance):
        try:
            distance = float(distance)
        except (TypeError, ValueError) as exc:
            raise ValueError("distance must be a positive number of millimeters") from exc
        if not math.isfinite(distance) or distance <= 0:
            raise ValueError("distance must be a positive number of millimeters")
        return distance


_DEFAULT_PORT = 5006
if __name__ == "__main__":
    from AFL.automation.shared.launcher import *
