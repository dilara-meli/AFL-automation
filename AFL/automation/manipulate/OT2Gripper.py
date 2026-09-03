"""Electrochemistry gripper workflows coordinated with an OT-2 gantry."""

from __future__ import annotations

import logging
import math
from typing import Any, Dict, Iterable, Optional

from AFL.automation.APIServer.Driver import Driver, ProxyConnectionError
from AFL.automation.APIServer.Client import Client
from AFL.automation.manipulate.OT2GantryDriver import OT2GantryDriver


class OT2Gripper(OT2GantryDriver):
    """Coordinate OT-2 motion with a remote ElectrochemGripper APIServer.

    This driver runs on the workflow host.  It owns electrode inventory and
    waits for the OT2Prepare and ElectrochemGripper queues between every step,
    but delegates physical servo control to the Raspberry Pi server.
    """

    defaults = {
        "gripper_ip": "10.42.0.231",
        "gripper_port": "5058",
        "gripper_mount": "left",
        "approach_z": None,
        "grip_z": None,
        "retract_z": None,
        "electrode_rack_slots": [],
        "available_electrodes": [],
        "held_electrode": None,
    }

    # The gripper first moves to this well-relative height before descending
    # toward an electrochemistry plate.  It is deliberately independent of
    # electrode-rack pickup calibration.
    WELL_APPROACH_Z = 170.0
    MIN_LABWARE_TOP_CLEARANCE_MM = 65.0

    def __init__(
        self,
        overrides: Optional[Dict[str, Any]] = None,
        ot2_prepare_ip: Optional[str] = None,
        ot2_prepare_port: Optional[int] = None,
        gripper_ip: Optional[str] = None,
        gripper_port: Optional[int] = None,
        afl_home: Optional[str] = None,
    ) -> None:
        overrides = dict(overrides or {})
        if gripper_ip is not None:
            overrides["gripper_ip"] = gripper_ip
        if gripper_port is not None:
            overrides["gripper_port"] = str(gripper_port)
        if ot2_prepare_ip is not None:
            overrides["ot2_prepare_ip"] = ot2_prepare_ip
        if ot2_prepare_port is not None:
            overrides["ot2_prepare_port"] = str(ot2_prepare_port)

        Driver.__init__(
            self,
            name="OT2Gripper",
            defaults=self.gather_defaults(),
            overrides=overrides,
            afl_home=afl_home,
        )
        OT2GantryDriver.__init__(
            self,
            overrides=overrides,
            ot2_prepare_ip=ot2_prepare_ip,
            ot2_prepare_port=ot2_prepare_port,
            initialize_driver=False,
        )
        gripper_mount = self._normalize_mount(self.config["gripper_mount"])
        # Inherited well-relative gantry commands should target the same
        # pipette that physically carries the remote gripper.
        self.config["gantry_reference_mount"] = gripper_mount
        self._gripper_client = None

    @Driver.queued()
    def register_electrode_racks(self, slots: Iterable[str]) -> Dict[str, Any]:
        """Register loaded OT2Prepare labware slots as electrode racks.

        Every well declared in each rack's loaded labware JSON becomes an
        available electrode. Registering racks is an explicit inventory reset.
        """
        self._ensure_no_held_electrode_for_reset()
        slots = self._normalize_slots(slots)
        available = self._electrodes_from_slots(self._owner_config(), slots)
        self.config["electrode_rack_slots"] = slots
        self.config["available_electrodes"] = available
        return self.status()

    @Driver.queued()
    def reset_electrode_racks(self) -> Dict[str, Any]:
        """Restore availability for all wells in the registered electrode racks."""
        self._ensure_no_held_electrode_for_reset()
        registered_slots = self.config["electrode_rack_slots"]
        if not registered_slots:
            raise RuntimeError("No electrode racks are registered. Call register_electrode_racks first.")
        slots = self._normalize_slots(registered_slots)
        self.config["available_electrodes"] = self._electrodes_from_slots(
            self._owner_config(), slots
        )
        return self.status()

    @Driver.queued()
    def pickup_electrode(self, location: Optional[str] = None) -> Dict[str, Any]:
        """Pick up a specified available electrode or the next available one."""
        if self.config["held_electrode"] is not None:
            raise RuntimeError("An electrode is already held. Drop it before picking up another.")
        heights = self._motion_heights()
        owner_config = self._owner_config()
        electrode = self._reserve_electrode(owner_config, location)

        self._run_gripper_command("set_angle", angle=60)
        self._move_and_wait(owner_config, electrode, heights["approach_z"])
        self._move_and_wait(owner_config, electrode, heights["grip_z"])
        self._run_gripper_command("close")
        self._move_and_wait(owner_config, electrode, heights["retract_z"])
        self.config["held_electrode"] = electrode
        return {"status": "picked_up", "electrode": electrode, "gripper": self.status()}

    @Driver.queued()
    def drop_electrode(self, location: str, offset_y: float = 0.0) -> Dict[str, Any]:
        """Move to ``location`` plus a Y offset, release, and retract.

        ``offset_y`` is a well-relative displacement in millimetres. It is
        applied to the approach, release, and retract positions so the
        electrode follows a vertical path at the requested offset.
        """
        held_electrode = self.config["held_electrode"]
        if held_electrode is None:
            raise RuntimeError("No electrode is held. Pick up an electrode before dropping one.")
        offset_y = self._finite_z(offset_y, "offset_y")
        heights = self._motion_heights()
        owner_config = self._owner_config()
        target = self._resolve_location(owner_config, location)
        self._move_and_wait(owner_config, target, heights["approach_z"], offset_y=offset_y)
        self._move_and_wait(owner_config, target, heights["grip_z"], offset_y=offset_y)
        self._run_gripper_command("open")
        self._move_and_wait(owner_config, target, heights["retract_z"], offset_y=offset_y)
        self.config["held_electrode"] = None
        return {
            "status": "dropped",
            "electrode": held_electrode,
            "location": target["location"],
            "offset_y": offset_y,
            "gripper": self.status(),
        }

    @Driver.queued()
    def move_electrode_to_well(self, location: str, experiment_z: float) -> Dict[str, Any]:
        """Move the held electrode safely to a loaded experiment-plate well.

        ``location`` uses the standard OT2Prepare ``"<slot><well>"`` form.
        The driver resolves that location against OT2Prepare's currently
        loaded labware, moves first to :attr:`WELL_APPROACH_Z`, waits for the
        move to complete, then descends to the requested well-relative
        ``experiment_z`` offset.
        """
        held_electrode = self.config["held_electrode"]
        if held_electrode is None:
            raise RuntimeError("No electrode is held. Pick up an electrode before moving to a well.")
        experiment_z = self._finite_z(experiment_z, "experiment_z")
        owner_config = self._owner_config()
        target = self._resolve_location(owner_config, location)
        self._move_and_wait(owner_config, target, self.WELL_APPROACH_Z)
        self._move_and_wait(owner_config, target, experiment_z)
        return {
            "status": "at_experiment_well",
            "electrode": held_electrode,
            "location": target["location"],
            "approach_z": self.WELL_APPROACH_Z,
            "experiment_z": experiment_z,
        }

    @Driver.unqueued()
    def status(self) -> Dict[str, Any]:
        """Return local coordination, inventory, and proxy-target state."""
        return {
            "gripper_mount": self.config["gripper_mount"],
            "gripper_server": {
                "ip": self.config["gripper_ip"],
                "port": str(self.config["gripper_port"]),
            },
            "electrode_rack_slots": list(self.config["electrode_rack_slots"]),
            "available_electrodes": list(self.config["available_electrodes"]),
            "available_electrode_count": len(self.config["available_electrodes"]),
            "held_electrode": self.config["held_electrode"],
        }

    def _get_gripper_client(self):
        if self._gripper_client is None:
            ip = self.config.get("gripper_ip")
            if not ip:
                raise ValueError("gripper_ip must name the ElectrochemGripper APIServer")
            self._gripper_client = self.get_proxy_client(
                "electrochem_gripper",
                ip=ip,
                port=str(self.config["gripper_port"]),
                username="OT2Gripper",
                client_factory=Client,
            )
        return self._gripper_client

    def _run_gripper_command(self, task_name: str, **kwargs) -> Dict[str, Any]:
        """Queue one Raspberry Pi gripper action and wait for its result."""
        try:
            client = self._get_gripper_client()
            task_uuid = client.enqueue(task_name=task_name, interactive=False, **kwargs)
            meta = client.wait(target_uuid=task_uuid, first_check_delay=0.0)
        except ProxyConnectionError:
            raise
        except Exception as exc:
            raise ProxyConnectionError(
                f"Unable to run {task_name!r} through the ElectrochemGripper proxy. "
                "The Raspberry Pi APIServer may be unavailable."
            ) from exc
        if not isinstance(meta, dict) or meta.get("exit_state") != "Success!":
            detail = meta.get("return_val") if isinstance(meta, dict) else meta
            raise RuntimeError(f"ElectrochemGripper task {task_uuid} failed: {detail}")
        return meta

    def _motion_heights(self) -> Dict[str, float]:
        heights = {}
        for key in ("approach_z", "grip_z", "retract_z"):
            heights[key] = self._finite_z(self.config[key], key)
        return heights

    @staticmethod
    def _finite_z(value: float, name: str) -> float:
        try:
            value = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{name} must be configured as a finite millimetre offset") from exc
        if not math.isfinite(value):
            raise ValueError(f"{name} must be configured as a finite millimetre offset")
        return value

    def _ensure_no_held_electrode_for_reset(self) -> None:
        if self.config["held_electrode"] is not None:
            raise RuntimeError("Cannot reset electrode racks while an electrode is held.")

    def _normalize_slots(self, slots: Iterable[str]) -> list[str]:
        if isinstance(slots, str):
            slots = [slots]
        try:
            normalized = [str(slot).strip() for slot in slots]
        except TypeError as exc:
            raise ValueError("slots must be a non-empty sequence of deck slot identifiers") from exc
        if not normalized or any(not slot.isdigit() for slot in normalized):
            raise ValueError("slots must be a non-empty sequence of deck slot identifiers")
        if len(set(normalized)) != len(normalized):
            raise ValueError("electrode rack slots must not contain duplicates")
        return normalized

    def _electrodes_from_slots(self, owner_config, slots: Iterable[str]) -> list[list[str]]:
        available = []
        for slot in slots:
            try:
                labware_id, _, labware_data = owner_config["loaded_labware"][slot]
                wells = labware_data["definition"]["wells"]
            except (KeyError, IndexError, TypeError) as exc:
                raise ValueError(
                    f"OT2Prepare has no JSON-defined labware with wells in electrode rack slot {slot!r}"
                ) from exc
            if not isinstance(wells, dict) or not wells:
                raise ValueError(f"Electrode rack slot {slot!r} defines no wells")
            available.extend([[labware_id, str(well).upper()] for well in wells])
        return available

    def _reserve_electrode(self, owner_config, location: Optional[str]) -> Dict[str, str]:
        available = list(self.config["available_electrodes"])
        if not available:
            raise RuntimeError("No electrodes are available. Register or reset electrode racks.")
        if location is None:
            labware_id, well_name = available[0]
            target = self._target_from_inventory_entry(owner_config, labware_id, well_name)
            index = 0
        else:
            target = self._resolve_location(owner_config, location)
            index = next(
                (
                    index
                    for index, entry in enumerate(available)
                    if len(entry) == 2
                    and entry[0] == target["labware_id"]
                    and str(entry[1]).upper() == target["well_name"]
                ),
                None,
            )
            if index is None:
                raise ValueError(f"Requested electrode location {target['location']} is not available")
        del available[index]
        self.config["available_electrodes"] = available
        return target

    def _target_from_inventory_entry(self, owner_config, labware_id: str, well_name: str) -> Dict[str, str]:
        for slot in self.config["electrode_rack_slots"]:
            labware = owner_config.get("loaded_labware", {}).get(str(slot))
            if labware and labware[0] == labware_id:
                return self._resolve_location(owner_config, f"{slot}{well_name}")
        raise RuntimeError(
            f"Registered electrode labware {labware_id!r} is no longer loaded in its configured slot"
        )

    def _resolve_location(self, owner_config, location: str) -> Dict[str, str]:
        slot, well_name = self._parse_location(location)
        try:
            labware_id, _, labware_data = owner_config["loaded_labware"][slot]
            wells = labware_data["definition"]["wells"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError(f"No loaded JSON-defined labware at {slot!r}") from exc
        if well_name not in wells:
            raise ValueError(f"Labware in slot {slot!r} has no well {well_name!r}")
        return {
            "slot": slot,
            "labware_id": labware_id,
            "well_name": well_name,
            "location": f"{slot}{well_name}",
        }

    def _move_and_wait(
        self,
        owner_config,
        target: Dict[str, str],
        z_offset: float,
        offset_y: float = 0.0,
    ) -> None:
        mount = self._normalize_mount(self.config["gripper_mount"])
        ot2_target = self._resolve_target(owner_config, target["slot"], target["well_name"], mount)
        task_uuid = self._enqueue_atomic_move(
            ot2_target,
            "top",
            {"x": 0.0, "y": offset_y, "z": z_offset},
        )
        client = self._get_ot2_prepare_client()
        meta = client.wait(target_uuid=task_uuid, first_check_delay=0.0)
        if not isinstance(meta, dict) or meta.get("exit_state") != "Success!":
            detail = meta.get("return_val") if isinstance(meta, dict) else meta
            raise RuntimeError(f"OT2 movement task {task_uuid} failed: {detail}")

    def _enqueue_atomic_move(self, target, origin, offset):
        """Queue only gripper moves that maintain labware-top clearance.

        OT2 well coordinates are only comparable to a labware top when using
        the ``top`` origin.  Other origins therefore cannot prove the required
        clearance and are rejected rather than risking a collision.
        """
        if str(origin).strip().lower() != "top":
            raise ValueError(
                "OT2Gripper safety check requires moves relative to the labware top"
            )
        try:
            z_offset = float(offset["z"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("OT2Gripper move must include a finite Z offset") from exc
        if not math.isfinite(z_offset):
            raise ValueError("OT2Gripper move must include a finite Z offset")
        if z_offset < self.MIN_LABWARE_TOP_CLEARANCE_MM:
            raise ValueError(
                "OT2Gripper safety check rejected move: gripper would be closer than "
                f"{self.MIN_LABWARE_TOP_CLEARANCE_MM:g} mm to the top of labware"
            )
        return super()._enqueue_atomic_move(target, origin, offset)


_DEFAULT_PORT = 5059
_DEFAULT_CUSTOM_CONFIG = {
    "_classname": "AFL.automation.manipulate.OT2Gripper.OT2Gripper",
    "overrides": {
        "gripper_ip": "10.42.0.231",
        "gripper_port": "5058",
        "gripper_mount": "right",
        "ot2_prepare_ip": "127.0.0.1",
        "ot2_prepare_port": "5002",
        "approach_z": "170",
        "grip_z": "97.5",
        "retract_z": "170",
        "log_level": logging.INFO,
    },
}


if __name__ == "__main__":
    from AFL.automation.shared.launcher import *
