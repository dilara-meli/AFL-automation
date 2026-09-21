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
        # All workflow moves translate in XY at this absolute gripper height
        # above the deck before moving vertically to their requested Z
        # coordinate relative to the target well top.
        "translate_z": 105.0,
        "grip_z": None,
        "retract_z": None,
        "electrode_rack_slots": [],
        "available_electrodes": [],
        "occupied_electrode_slots": [],
        "used_electrode_slots": [],
        # Completed ASV/measurement runs keyed by stable rack-slot/well ID.
        "electrode_measurement_counts": {},
        "held_electrode": None,
    }

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
        # ``approach_z`` was replaced by the gripper workflow's translate
        # height. Remove it from persistent configurations created by older
        # versions.
        if "approach_z" in self.config:
            del self.config["approach_z"]
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
        # Last completed physical gripper position. This lets each subsequent
        # movement explicitly retract before translating in XY.
        self._gripper_motion_state = None

    @Driver.queued()
    def register_electrode_racks(self, slots: Iterable[str]) -> Dict[str, Any]:
        """Register loaded OT2Prepare labware slots as electrode racks.

        Every well declared in each rack's loaded labware JSON becomes an
        available electrode. Registering racks is an explicit inventory reset.
        """
        self._ensure_no_held_electrode_for_reset()
        slots = self._normalize_slots(slots)
        occupied = self._electrodes_from_slots(self._owner_config(), slots)
        self.config["electrode_rack_slots"] = slots
        self._set_occupied_electrode_slots(occupied)
        return self.status()

    @Driver.queued()
    def reset_electrode_racks(self) -> Dict[str, Any]:
        """Restore availability for all wells in the registered electrode racks."""
        self._ensure_no_held_electrode_for_reset()
        registered_slots = self.config["electrode_rack_slots"]
        if not registered_slots:
            raise RuntimeError("No electrode racks are registered. Call register_electrode_racks first.")
        slots = self._normalize_slots(registered_slots)
        self._set_occupied_electrode_slots(
            self._electrodes_from_slots(self._owner_config(), slots)
        )
        return self.status()

    @Driver.queued()
    def pickup_electrode(
        self,
        location: Optional[str] = None,
        reuse: bool = False,
        offset_x: float = 0.0,
        offset_y: float = 0.0,
    ) -> Dict[str, Any]:
        """Pick up an occupied unused electrode, or reuse one when requested.

        Set ``reuse=True`` to allow selection of an electrode that was picked
        up previously and then returned to its origin slot. ``offset_x`` and
        ``offset_y`` are well-relative millimetre offsets applied to the
        translation, grip, and retract moves.
        """
        if self.config["held_electrode"] is not None:
            raise RuntimeError("An electrode is already held. Drop it before picking up another.")
        reuse = self._normalize_reuse(reuse)
        offset_x = self._finite_z(offset_x, "offset_x")
        offset_y = self._finite_z(offset_y, "offset_y")
        heights = self._motion_heights()
        owner_config = self._owner_config()
        electrode = self._reserve_electrode(owner_config, location, reuse=reuse)
        measurement_metadata = self._reserve_electrode_measurement(electrode)

        self._run_gripper_command("set_angle", angle=50)
        self._move_and_wait(
            owner_config,
            electrode,
            heights["grip_z"],
            offset_x=offset_x,
            offset_y=offset_y,
        )
        self._run_gripper_command("close")
        self._mark_electrode_used(electrode)
        self._move_and_wait(
            owner_config,
            electrode,
            heights["retract_z"],
            offset_x=offset_x,
            offset_y=offset_y,
        )
        self.config["held_electrode"] = electrode
        return {
            "status": "picked_up",
            "electrode": electrode,
            "measurement_metadata": measurement_metadata,
            "offset_x": offset_x,
            "offset_y": offset_y,
            "gripper": self.status(),
        }

    @Driver.queued()
    def complete_electrode_measurement(self) -> Dict[str, Any]:
        """Commit the held electrode's reserved measurement-use index.

        Call this only after the final measurement task (for example, DPV)
        succeeds. Repeating the call for the same held electrode is idempotent.
        """
        electrode = self.config["held_electrode"]
        if electrode is None:
            raise RuntimeError("No electrode is held. Pick up an electrode before completing a measurement.")

        metadata = self._electrode_measurement_metadata(electrode)
        if electrode.get("measurement_committed", False):
            return {"status": "already_completed", **metadata}

        counts = dict(self.config.get("electrode_measurement_counts", {}))
        electrode_id = metadata["electrode_id"]
        counts[electrode_id] = metadata["electrode_use_index"]
        self.config["electrode_measurement_counts"] = counts
        electrode["measurement_committed"] = True
        self.config["held_electrode"] = electrode
        return {"status": "completed", **metadata}

    @Driver.queued()
    def drop_electrode(
        self,
        location: Optional[str] = None,
        offset_y: float = 0.0,
        waste: bool = False,
        offset_x: float = 0.0,
    ) -> Dict[str, Any]:
        """Return a held electrode to its origin, or discard it in a waste well.

        A normal drop may only return the electrode to its pickup location,
        which is the only electrode-rack well made unoccupied by pickup. Set
        ``waste=True`` and provide a loaded waste-well location to discard the
        electrode without reoccupying its origin slot. ``offset_x`` and
        ``offset_y`` are well-relative millimetre offsets applied to the
        translation, release, and retract moves.
        """
        held_electrode = self.config["held_electrode"]
        if held_electrode is None:
            raise RuntimeError("No electrode is held. Pick up an electrode before dropping one.")
        offset_x = self._finite_z(offset_x, "offset_x")
        offset_y = self._finite_z(offset_y, "offset_y")
        heights = self._motion_heights()
        owner_config = self._owner_config()
        if waste:
            if location is None or not str(location).strip():
                raise ValueError("A waste location is required when waste=True")
            target = self._resolve_location(owner_config, location)
        else:
            origin = held_electrode["location"]
            if location is not None and str(location).strip().upper() != origin.upper():
                raise RuntimeError(
                    f"Electrode can only be returned to its origin slot {origin}; "
                    "use waste=True to discard it elsewhere."
                )
            target = self._resolve_location(owner_config, origin)
        self._move_and_wait(
            owner_config,
            target,
            heights["grip_z"],
            offset_x=offset_x,
            offset_y=offset_y,
        )
        self._run_gripper_command("open")
        # Opening releases the electrode. Restore only a normal return before
        # retracting so a retract failure cannot leave its origin unoccupied.
        if not waste:
            self._mark_electrode_slot_occupied(held_electrode)
        self._move_and_wait(
            owner_config,
            target,
            heights["retract_z"],
            offset_x=offset_x,
            offset_y=offset_y,
        )
        self.config["held_electrode"] = None
        return {
            "status": "dropped",
            "electrode": held_electrode,
            "location": target["location"],
            "offset_x": offset_x,
            "offset_y": offset_y,
            "gripper": self.status(),
        }

    @Driver.queued()
    def move_electrode_to_well(
        self,
        location: str,
        experiment_z: float,
        offset_x: float = 0.0,
        offset_y: float = 0.0,
    ) -> Dict[str, Any]:
        """Move the held electrode safely to a loaded experiment-plate well.

        ``location`` uses the standard OT2Prepare ``"<slot><well>"`` form.
        The driver resolves that location against OT2Prepare's currently
        loaded labware, translates laterally at ``translate_z``, then descends
        to the requested well-relative ``experiment_z`` offset. ``offset_x``
        and ``offset_y`` are well-relative millimetre offsets applied to both
        the translation and the vertical move.
        """
        held_electrode = self.config["held_electrode"]
        if held_electrode is None:
            raise RuntimeError("No electrode is held. Pick up an electrode before moving to a well.")
        experiment_z = self._finite_z(experiment_z, "experiment_z")
        offset_x = self._finite_z(offset_x, "offset_x")
        offset_y = self._finite_z(offset_y, "offset_y")
        owner_config = self._owner_config()
        target = self._resolve_location(owner_config, location)
        self._move_and_wait(
            owner_config,
            target,
            experiment_z,
            offset_x=offset_x,
            offset_y=offset_y,
        )
        return {
            "status": "at_experiment_well",
            "electrode": held_electrode,
            "location": target["location"],
            "translate_z": self._translate_z(),
            "experiment_z": experiment_z,
            "offset_x": offset_x,
            "offset_y": offset_y,
        }

    def move_to_well(
        self,
        location,
        origin="top",
        offset_x=None,
        offset_y=None,
        offset_z=None,
    ):
        """Disallow raw gantry moves that bypass gripper approach sequencing."""
        raise RuntimeError(
            "OT2Gripper direct gantry moves are disabled; use the electrode workflow methods."
        )

    def move_pipette(self, mount="", dx=0.0, dy=0.0, dz=0.0):
        """Disallow raw relative moves that bypass gripper approach sequencing."""
        raise RuntimeError(
            "OT2Gripper direct gantry moves are disabled; use the electrode workflow methods."
        )

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
            "occupied_electrode_slots": list(self.config["occupied_electrode_slots"]),
            "used_electrode_slots": list(self.config["used_electrode_slots"]),
            "electrode_measurement_counts": dict(
                self.config["electrode_measurement_counts"]
            ),
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
        for key in ("grip_z", "retract_z"):
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

    def _set_occupied_electrode_slots(self, occupied: list[list[str]]) -> None:
        """Persist occupied source wells and the legacy availability view."""
        occupied = [list(entry) for entry in occupied]
        self.config["occupied_electrode_slots"] = occupied
        self.config["available_electrodes"] = list(occupied)

    def _mark_electrode_slot_occupied(self, electrode: Dict[str, str]) -> None:
        occupied = list(self.config["occupied_electrode_slots"])
        key = [electrode["labware_id"], electrode["well_name"]]
        if key not in occupied:
            occupied.append(key)
        self._set_occupied_electrode_slots(occupied)

    def _mark_electrode_used(self, electrode: Dict[str, str]) -> None:
        used = list(self.config["used_electrode_slots"])
        key = [electrode["labware_id"], electrode["well_name"]]
        if key not in used:
            used.append(key)
            self.config["used_electrode_slots"] = used

    @staticmethod
    def _electrode_id(electrode: Dict[str, Any]) -> str:
        """Build the persistent identity for an electrode stored in a rack well."""
        try:
            return f"{electrode['slot']}:{electrode['well_name']}"
        except KeyError as exc:
            raise ValueError("Electrode metadata has no rack slot or well name") from exc

    def _electrode_measurement_metadata(self, electrode: Dict[str, Any]) -> Dict[str, Any]:
        """Return metadata for the held electrode's reserved measurement use."""
        electrode_id = self._electrode_id(electrode)
        completed_count = int(
            self.config.get("electrode_measurement_counts", {}).get(electrode_id, 0)
        )
        use_index = electrode.get("electrode_use_index", completed_count + 1)
        return {
            "electrode_id": electrode_id,
            "electrode_origin_location": electrode["location"],
            "electrode_use_index": int(use_index),
            "electrode_completed_measurement_count": completed_count,
        }

    def _reserve_electrode_measurement(self, electrode: Dict[str, Any]) -> Dict[str, Any]:
        """Reserve, without committing, the next measurement index for an electrode."""
        metadata = self._electrode_measurement_metadata(electrode)
        electrode["electrode_use_index"] = metadata["electrode_use_index"]
        electrode["measurement_committed"] = False
        return metadata

    @staticmethod
    def _normalize_reuse(reuse: bool) -> bool:
        if isinstance(reuse, str):
            normalized = reuse.strip().lower()
            if normalized in {"true", "1", "yes"}:
                return True
            if normalized in {"false", "0", "no", ""}:
                return False
            raise ValueError("reuse must be a boolean")
        if isinstance(reuse, bool):
            return reuse
        raise ValueError("reuse must be a boolean")

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

    def _reserve_electrode(
        self, owner_config, location: Optional[str], reuse: bool = False
    ) -> Dict[str, str]:
        occupied = list(self.config["occupied_electrode_slots"])
        if not occupied:
            raise RuntimeError("No electrodes are available. Register or reset electrode racks.")
        used = list(self.config["used_electrode_slots"])
        eligible = occupied if reuse else [entry for entry in occupied if entry not in used]
        if not eligible:
            raise RuntimeError(
                "No unused occupied electrodes are available. Pass reuse=True to reuse an electrode."
            )
        if location is None:
            labware_id, well_name = eligible[0]
            target = self._target_from_inventory_entry(owner_config, labware_id, well_name)
        else:
            target = self._resolve_location(owner_config, location)
            key = [target["labware_id"], target["well_name"]]
            if key not in occupied:
                raise ValueError(f"Requested electrode location {target['location']} is not available")
            if key in used and not reuse:
                raise RuntimeError(
                    f"Electrode at {target['location']} has already been used; pass reuse=True to pick it again."
                )
        key = [target["labware_id"], target["well_name"]]
        occupied.remove(key)
        self._set_occupied_electrode_slots(occupied)
        return target

    def _target_from_inventory_entry(self, owner_config, labware_id: str, well_name: str) -> Dict[str, Any]:
        for slot in self.config["electrode_rack_slots"]:
            labware = owner_config.get("loaded_labware", {}).get(str(slot))
            if labware and labware[0] == labware_id:
                return self._resolve_location(owner_config, f"{slot}{well_name}")
        raise RuntimeError(
            f"Registered electrode labware {labware_id!r} is no longer loaded in its configured slot"
        )

    def _resolve_location(self, owner_config, location: str) -> Dict[str, Any]:
        slot, well_name = self._parse_location(location)
        if str(slot) in owner_config.get("loaded_modules", {}):
            raise ValueError(
                f"Cannot determine the deck height of labware loaded on module slot {slot!r}"
            )
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
            "well_top_z": self._well_top_z(wells[well_name], f"{slot}{well_name}"),
        }

    def _well_top_z(self, well: Dict[str, Any], label: str) -> float:
        """Return a well-top height above the deck from loaded labware geometry."""
        try:
            bottom_z = float(well["z"])
            depth = float(well["depth"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(
                f"Labware well {label!r} has no usable z and depth geometry"
            ) from exc
        if not math.isfinite(bottom_z) or not math.isfinite(depth) or depth < 0:
            raise ValueError(f"Labware well {label!r} has invalid z or depth geometry")
        return bottom_z + depth

    def _validate_translate_clearance(
        self, owner_config: Dict[str, Any], translate_z: float, attachment: Dict[str, float]
    ) -> None:
        """Prove the deck-height translation plane clears every loaded labware well."""
        try:
            loaded_labware = owner_config["loaded_labware"]
        except KeyError as exc:
            raise ValueError("OT2Prepare configuration has no loaded labware") from exc
        if not isinstance(loaded_labware, dict):
            raise ValueError("OT2Prepare loaded labware configuration is invalid")

        highest_well_top = None
        for slot, labware in loaded_labware.items():
            if str(slot) in owner_config.get("loaded_modules", {}):
                raise ValueError(
                    f"Cannot determine the deck height of labware loaded on module slot {slot!r}"
                )
            try:
                wells = labware[2]["definition"]["wells"]
            except (IndexError, KeyError, TypeError) as exc:
                raise ValueError(
                    f"Loaded labware in slot {slot!r} has no usable well geometry"
                ) from exc
            if not isinstance(wells, dict) or not wells:
                raise ValueError(
                    f"Loaded labware in slot {slot!r} has no usable well geometry"
                )
            for well_name, well in wells.items():
                well_top = self._well_top_z(well, f"{slot}{well_name}")
                highest_well_top = (
                    well_top
                    if highest_well_top is None
                    else max(highest_well_top, well_top)
                )

        required_height = highest_well_top + attachment["horizontal_clearance_mm"]
        if translate_z < required_height:
            raise ValueError(
                "Gripper translate_z does not clear loaded labware: "
                f"{translate_z:g} mm is below the required {required_height:g} mm"
            )

    def _move_and_wait(
        self,
        owner_config,
        target: Dict[str, Any],
        offset_z: float,
        offset_x: float = 0.0,
        offset_y: float = 0.0,
    ) -> None:
        offset_z = self._finite_z(offset_z, "offset_z")
        offset_x = self._finite_z(offset_x, "offset_x")
        offset_y = self._finite_z(offset_y, "offset_y")
        attachment = self._gripper_attachment(owner_config)
        translate_z = self._translate_z()
        self._validate_translate_clearance(owner_config, translate_z, attachment)
        desired_offset = {"x": offset_x, "y": offset_y, "z": offset_z}
        target_translate_offset = translate_z - target["well_top_z"]
        desired_absolute_z = target["well_top_z"] + offset_z

        # First return vertically to the fixed deck-height translation plane
        # at the current XY position whenever a previous move ended elsewhere.
        current = self._gripper_motion_state
        if current is not None:
            current_absolute_z = current.get(
                "absolute_z", current["target"]["well_top_z"] + current["offset"]["z"]
            )
        else:
            current_absolute_z = None
        if current is not None and current_absolute_z != translate_z:
            self._enqueue_gripper_move(owner_config, current["target"], {
                "x": current["offset"]["x"],
                "y": current["offset"]["y"],
                "z": translate_z - current["target"]["well_top_z"],
            })

        # Every workflow movement explicitly translates to its target XY at
        # translate_z, including when the target matches local motion state.
        # This keeps the high-level workflow contract independent of callers.
        self._enqueue_gripper_move(
            owner_config,
            target,
            {"x": offset_x, "y": offset_y, "z": target_translate_offset},
        )

        # Move vertically only after the XY translation has completed.
        if desired_absolute_z != translate_z:
            self._enqueue_gripper_move(owner_config, target, desired_offset)

    def _enqueue_gripper_move(self, owner_config, target: Dict[str, Any], offset: Dict[str, float]) -> None:
        """Submit one completed gripper move and update the physical position."""
        mount = self._normalize_mount(self.config["gripper_mount"])
        attachment = self._gripper_attachment(owner_config)
        ot2_target = self._resolve_target(owner_config, target["slot"], target["well_name"], mount)
        # The profile offset is expressed as the gripper's lowest-point Z
        # relative to the pipette tip. Convert the requested physical gripper
        # coordinate into the pipette coordinate expected by Opentrons.
        pipette_offset = dict(offset)
        pipette_offset["z"] = offset["z"] - attachment["gripper_tip_z_offset_mm"]
        task_uuid = self._enqueue_atomic_move(ot2_target, "top", pipette_offset)
        client = self._get_ot2_prepare_client()
        meta = client.wait(target_uuid=task_uuid, first_check_delay=0.0)
        if not isinstance(meta, dict) or meta.get("exit_state") != "Success!":
            detail = meta.get("return_val") if isinstance(meta, dict) else meta
            raise RuntimeError(f"OT2 movement task {task_uuid} failed: {detail}")
        self._gripper_motion_state = {
            "target": dict(target),
            "offset": dict(offset),
            "absolute_z": target["well_top_z"] + offset["z"],
        }

    def _gripper_attachment(self, owner_config) -> Dict[str, float]:
        mount = self._normalize_mount(self.config["gripper_mount"])
        try:
            attachment = dict(owner_config["loaded_gripper_attachments"][mount])
            attachment["gripper_tip_z_offset_mm"] = float(
                attachment["gripper_tip_z_offset_mm"]
            )
            attachment["horizontal_clearance_mm"] = float(
                attachment["horizontal_clearance_mm"]
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError(
                f"OT2Gripper requires a calibrated gripper attachment on the {mount!r} mount"
            ) from exc
        return attachment

    def _translate_z(self) -> float:
        """Return the fixed, deck-relative gripper height used for XY translation."""
        return self._finite_z(self.config["translate_z"], "translate_z")

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
            offset_z = float(offset["z"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("OT2Gripper move must include a finite Z offset") from exc
        if not math.isfinite(offset_z):
            raise ValueError("OT2Gripper move must include a finite Z offset")
        return super()._enqueue_atomic_move(target, origin, offset)


_DEFAULT_PORT = 5059
_DEFAULT_CUSTOM_CONFIG = {
    "_classname": "AFL.automation.manipulate.OT2Gripper.OT2Gripper",
    "overrides": {
        "gripper_ip": "10.42.0.231",
        "gripper_port": "5058",
        "gripper_mount": "right",
        "translate_z": "105",
        "ot2_prepare_ip": "127.0.0.1",
        "ot2_prepare_port": "5002",
        "grip_z": "10",
        "retract_z": "105",
        "log_level": logging.INFO,
    },
}


if __name__ == "__main__":
    from AFL.automation.shared.launcher import *
