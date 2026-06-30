import warnings

from AFL.automation.prepare.OT2HTTPDriver import OT2HTTPDriver
from AFL.automation.prepare.PrepareDriver import PrepareDriver
from AFL.automation.shared.utilities import listify


class OT2Prepare(OT2HTTPDriver, PrepareDriver):
    """Preparation-oriented OT-2 driver.

    This class combines :class:`OT2HTTPDriver` transport primitives with the
    higher-level preparation workflow implemented by :class:`PrepareDriver`.
    It adds stock-aware tip reservation, destination occupancy tracking, and
    execution helpers for preparation plans.

    Parameters
    ----------
    overrides : dict, optional
        Configuration overrides merged into the inherited defaults.

    Examples
    --------
    >>> driver = OT2Prepare({"robot_ip": "192.168.1.50"})
    >>> driver.add_prep_targets(["4A1", "4A2"])
    >>> driver.resolve_destination(None)
    '4A1'
    """
    defaults = {
        "prep_targets": [],
        "prepare_volume": "900 ul",
        "catch_volume": "900 ul",
        "deck": {},
        "stocks": [],
        "stock_mix_order": [],
        "fixed_compositions": {},
        "stock_locations": {},  # Maps stock names to deck positions: {'stockH2O': '3A2'}
        "stock_transfer_params": {},  # Per-stock transfer parameters: {'stockH2O': {'mix_after': True}}
        "catch_protocol": {},  # PipetteAction-formatted dict for catch transfer parameters
    }

    def __init__(self, overrides=None):
        """Initialize the preparation driver.

        Parameters
        ----------
        overrides : dict, optional
            Configuration values applied on top of the inherited defaults.

        Examples
        --------
        >>> driver = OT2Prepare({"prepare_volume": "500 ul"})
        >>> driver.last_target_location is None
        True
        """
        OT2HTTPDriver.__init__(self, overrides=overrides)
        PrepareDriver.__init__(self, driver_name="OT2Prepare", overrides=overrides)
        self.last_target_location = None
        self.useful_links["View Deck"] = "/visualize_deck"

    def status(self):
        """Return combined preparation and robot status lines.

        Returns
        -------
        list of str
            Human-readable status lines from both parent driver layers.

        Examples
        --------
        >>> isinstance(driver.status(), list)
        True
        """
        return PrepareDriver.status(self) + OT2HTTPDriver.status(self)

    def _status_lines(self):
        """Build preparation-specific status lines.

        Returns
        -------
        list of str
            Summary lines describing configured stocks, reserved stock tips,
            occupied sample locations, and queued preparation targets.

        Examples
        --------
        >>> lines = driver._status_lines()
        >>> isinstance(lines, list)
        True
        """
        status = []
        status.append(f"Stocks: {len(self.stocks)} configured")
        status.append(f"Stock locations: {self.config['stock_locations']}")
        status.append(
            f"Stock-reserved tips: {len(self.config.get('reserved_stock_tips', []))}"
        )
        status.append(
            f"Occupied sample locations: {len(self.config.get('occupied_sample_locations', []))}"
        )
        status.append(f"{len(self.config['prep_targets'])} preparation targets available")
        return status

    def _normalize_locations(self, locations):
        """Normalize and deduplicate deck locations.

        Parameters
        ----------
        locations : iterable of str
            Deck locations such as ``"4A1"`` or ``"6B3"``.

        Returns
        -------
        list of str
            Uppercase normalized locations in first-seen order.

        Examples
        --------
        >>> driver._normalize_locations(["4a1", "4A1", "4b1"])
        ['4A1', '4B1']
        """
        normalized = []
        for location in locations:
            normalized_location = self._normalize_deck_location(location)
            if normalized_location not in normalized:
                normalized.append(normalized_location)
        return normalized

    def _sync_stock_tip_tracking(self):
        """Rebuild stock-tip configuration and active reservations.

        Notes
        -----
        This method inspects configured stock objects for ``tip_location`` or
        legacy ``tip`` attributes and synchronizes the persistent reservation
        state stored in the driver config.

        Examples
        --------
        >>> driver._sync_stock_tip_tracking()
        >>> isinstance(driver.config.get("stock_tip_locations", {}), dict)
        True
        """
        stock_tip_locations = {}
        for stock in getattr(self, "stocks", []):
            tip_location = getattr(stock, "tip_location", None) or getattr(stock, "tip", None)
            if tip_location is None:
                continue
            stock_tip_locations[stock.name] = self._normalize_locations(listify(tip_location))

        existing_reservations = self.config.get("stock_tip_reservations", {})
        normalized_reservations = {}
        for stock_name, tip_locations in existing_reservations.items():
            configured_tips = stock_tip_locations.get(stock_name, [])
            if not configured_tips:
                continue
            active = [
                location
                for location in self._normalize_locations(listify(tip_locations))
                if location in configured_tips
            ]
            if active:
                normalized_reservations[stock_name] = active

        active_reserved = []
        for tip_locations in normalized_reservations.values():
            active_reserved.extend(tip_locations)

        self.config["stock_tip_locations"] = stock_tip_locations
        self.config["stock_tip_reservations"] = normalized_reservations
        self.config["reserved_stock_tips"] = self._normalize_locations(active_reserved)

    def _ordered_stock_tip_candidates(self, stock_name, step_tip_location=None):
        """Return prioritized tip candidates for a stock transfer.

        Parameters
        ----------
        stock_name : str
            Stock identifier used in the preparation configuration.
        step_tip_location : str or sequence of str, optional
            Explicit tip location override from a protocol step.

        Returns
        -------
        list of str
            Normalized tip locations with active reservations ordered before
            configured fallback candidates.

        Examples
        --------
        >>> driver._ordered_stock_tip_candidates("stockH2O")
        ['6A1']
        """
        configured = self.config.get("stock_tip_locations", {}).get(stock_name, [])
        if step_tip_location is None:
            candidates = list(configured)
        else:
            candidates = self._normalize_locations(listify(step_tip_location))

        active = self.config.get("stock_tip_reservations", {}).get(stock_name, [])
        ordered = []
        for location in list(active) + list(candidates):
            normalized = self._normalize_deck_location(location)
            if normalized not in ordered:
                ordered.append(normalized)
        return ordered

    def _select_stock_tip_location(self, stock_name, volume_ul, step_tip_location=None):
        """Choose an available stock-reserved tip location.

        Parameters
        ----------
        stock_name : str
            Stock identifier.
        volume_ul : float
            Transfer volume used to determine the pipette mount.
        step_tip_location : str or sequence of str, optional
            Explicit tip location override from the planned transfer step.

        Returns
        -------
        str or None
            Selected normalized tip location, or ``None`` when no stock tip is
            configured.

        Raises
        ------
        ValueError
            If configured stock tips exist but none are currently available for
            the required mount.

        Examples
        --------
        >>> driver._select_stock_tip_location("stockH2O", 50)
        '6A1'
        """
        candidates = self._ordered_stock_tip_candidates(stock_name, step_tip_location)
        if not candidates:
            return None

        pipette_mount = self.get_pipette(float(volume_ul))["mount"]
        compatible = []
        for location in candidates:
            try:
                matches_mount = self._tip_location_matches_mount(pipette_mount, location)
            except ValueError:
                continue
            if not matches_mount:
                continue
            compatible.append(location)
            try:
                self._resolve_tip_location(pipette_mount, location)
                return location
            except ValueError:
                continue

        if compatible:
            raise ValueError(
                f"No configured tip locations for stock '{stock_name}' are currently available "
                f"on {pipette_mount} mount: {', '.join(compatible)}"
            )
        raise ValueError(
            f"No configured tip locations for stock '{stock_name}' match the {pipette_mount} mount: "
            f"{', '.join(candidates)}"
        )

    def _activate_stock_tip_reservation(self, stock_name, tip_location):
        """Mark a stock tip as actively reserved.

        Parameters
        ----------
        stock_name : str
            Stock identifier.
        tip_location : str or None
            Tip location to reserve. ``None`` leaves the reservation state
            unchanged.

        Examples
        --------
        >>> driver._activate_stock_tip_reservation("stockH2O", "6A1")
        """
        if stock_name is None or tip_location is None:
            return

        tip_location = self._normalize_deck_location(tip_location)
        configured = self.config.get("stock_tip_locations", {}).get(stock_name, [])
        if tip_location not in configured:
            return

        reservations = {
            name: self._normalize_locations(listify(locations))
            for name, locations in self.config.get("stock_tip_reservations", {}).items()
        }
        for other_stock, locations in reservations.items():
            if other_stock != stock_name and tip_location in locations:
                raise ValueError(
                    f"Tip location {tip_location} is already reserved for stock '{other_stock}' "
                    f"and cannot also be reserved for stock '{stock_name}'."
                )

        stock_reservations = reservations.get(stock_name, [])
        if tip_location not in stock_reservations:
            stock_reservations.append(tip_location)
        reservations[stock_name] = self._normalize_locations(stock_reservations)

        active_reserved = []
        for locations in reservations.values():
            active_reserved.extend(locations)

        self.config["stock_tip_reservations"] = reservations
        self.config["reserved_stock_tips"] = self._normalize_locations(active_reserved)

    def _build_stock_transfer_params(self, stock_name, volume_ul, step_tip_location=None):
        """Build transfer keyword arguments for a stock step.

        Parameters
        ----------
        stock_name : str
            Stock identifier.
        volume_ul : float
            Requested transfer volume in microliters.
        step_tip_location : str or sequence of str, optional
            Explicit tip location override from the protocol step.

        Returns
        -------
        tuple
            Two-item tuple ``(transfer_params, selected_tip_location)``.

        Examples
        --------
        >>> params, tip = driver._build_stock_transfer_params("stockH2O", 100)
        >>> isinstance(params, dict)
        True
        """
        transfer_params = self.get_transfer_params(stock_name)
        selected_tip_location = self._select_stock_tip_location(
            stock_name=stock_name,
            volume_ul=volume_ul,
            step_tip_location=step_tip_location,
        )
        if selected_tip_location is not None:
            transfer_params["tip_location"] = selected_tip_location
        return transfer_params, selected_tip_location

    def _occupied_sample_locations(self):
        """Return occupied sample destinations as a normalized set.

        Returns
        -------
        set of str
            Occupied destination locations currently tracked in config.

        Examples
        --------
        >>> isinstance(driver._occupied_sample_locations(), set)
        True
        """
        return set(self._normalize_locations(self.config.get("occupied_sample_locations", [])))

    def _assert_destination_locations_available(self, destinations):
        """Validate that destination locations are not already occupied.

        Parameters
        ----------
        locations : iterable of str
            Destination locations to validate.

        Raises
        ------
        ValueError
            If duplicate destinations are requested or any destination is
            already marked occupied.

        Examples
        --------
        >>> driver._assert_destination_locations_available(["4A1", "4A2"])
        """
        normalized = self._normalize_locations(listify(destinations))
        duplicates = sorted({location for location in normalized if normalized.count(location) > 1})
        if duplicates:
            raise ValueError(
                "Preparation requested the same destination location more than once: "
                + ", ".join(duplicates)
            )

        occupied = self._occupied_sample_locations()
        conflicts = [location for location in normalized if location in occupied]
        if conflicts:
            raise ValueError(
                "Destination location(s) already contain a prepared sample: "
                + ", ".join(conflicts)
                + ". Clear or change those sample destinations before preparing again."
            )

    def _mark_sample_locations_occupied(self, locations):
        """Record destination locations as occupied.

        Parameters
        ----------
        locations : iterable of str
            Locations to add to the occupied-sample tracking list.

        Examples
        --------
        >>> driver._mark_sample_locations_occupied(["4A1"])
        """
        occupied = self._occupied_sample_locations()
        for location in locations:
            if location not in occupied:
                occupied.append(location)
        self.config["occupied_sample_locations"] = occupied

    def clear_sample_locations(self, locations=None):
        """Clear occupied sample destination tracking.

        Parameters
        ----------
        locations : str or sequence of str, optional
            Specific occupied locations to clear. If omitted, all occupied
            sample locations are cleared.

        Returns
        -------
        list of str
            Normalized locations that were cleared.

        Examples
        --------
        >>> driver.clear_sample_locations(["4A1"])
        ['4A1']
        >>> driver.clear_sample_locations()
        []
        """
        occupied = self._occupied_sample_locations()
        if locations is None:
            self.config["occupied_sample_locations"] = []
            self.config._update_history()
            return []

        to_clear = set(self._normalize_locations(listify(locations)))
        remaining = [location for location in occupied if location not in to_clear]
        cleared = [location for location in occupied if location in to_clear]
        self.config["occupied_sample_locations"] = remaining
        self.config._update_history()
        return cleared

    def resolve_destination(self, destination):
        """Resolve the destination well for a preparation.

        Parameters
        ----------
        destination : str or None
            Explicit destination location. If ``None``, the next queued
            preparation target is consumed.

        Returns
        -------
        str
            Normalized destination location.

        Raises
        ------
        ValueError
            If no destination is available or the destination is already
            occupied.

        Examples
        --------
        >>> driver.resolve_destination("4A1")
        '4A1'
        """
        if destination is None:
            if not self.config.get("prep_targets"):
                raise ValueError("No preparation targets configured. Cannot select a destination target.")
            prep_targets = self.config["prep_targets"]
            destination = self._normalize_deck_location(prep_targets[0])
            self._assert_destination_locations_available([destination])
            prep_targets.pop(0)
            self.config["prep_targets"] = prep_targets
            return destination
        if not self.config.get("prep_targets"):
            raise ValueError("No preparation targets configured. Cannot select a destination target.")
        prep_targets = self.config["prep_targets"]
        destination = self._normalize_deck_location(prep_targets[0])
        self._assert_destination_locations_available([destination])
        prep_targets.pop(0)
        self.config["prep_targets"] = prep_targets
        return destination

    def _reserve_destinations(self, destination, intermediate_destinations=None):
        """Reserve final and intermediate destinations for a preparation plan.

        Parameters
        ----------
        destination : str
            Final destination location.
        intermediate_destinations : sequence of str, optional
            Intermediate destination locations used by staged plans.

        Returns
        -------
        tuple
            Normalized ``(destination, intermediate_destinations)``.

        Raises
        ------
        ValueError
            If any requested destination is already occupied.

        Examples
        --------
        >>> driver._reserve_destinations("4A1", ["5A1"])
        ('4A1', ['5A1'])
        """
        intermediate_destinations = list(intermediate_destinations or [])
        destination, intermediate_destinations, consumed, queue_key = super()._reserve_destinations(
            dest=destination,
            required_intermediate_targets=len(intermediate_destinations),
        )
        all_destinations = list(intermediate_destinations) + [destination]
        normalized_destinations = self._normalize_locations(all_destinations)
        normalized_intermediates = normalized_destinations[: len(intermediate_destinations)]
        normalized_destination = normalized_destinations[-1]
        try:
            self._assert_destination_locations_available(normalized_destinations)
        except Exception:
            if required_intermediate_targets > 0:
                self._restore_reserved_destinations(queue_key=queue_key, consumed=consumed)
            elif dest is None:
                queue = list(self.config.get("prep_targets", []))
                self.config["prep_targets"] = [normalized_destination] + queue
            raise
        return normalized_destination, normalized_intermediates, consumed, queue_key

    def execute_preparation(self, target, balanced_target, destination):
        """Execute a simple preparation protocol into one destination.

        Parameters
        ----------
        target : object
            Original target specification from the preparation workflow.
        balanced_target : object
            Balanced target object containing a generated ``protocol``.
        destination : str
            Destination deck location.

        Returns
        -------
        bool
            ``True`` when all transfers succeed, otherwise ``False``.

        Raises
        ------
        ValueError
            If no protocol is available or a stock location cannot be resolved.

        Examples
        --------
        >>> driver.execute_preparation(target, balanced_target, "4A1")
        True
        """
        if not hasattr(balanced_target, "protocol") or not balanced_target.protocol:
            raise ValueError("No protocol generated for the target solution")

        protocol = self.reorder_protocol(balanced_target.protocol)
        for step in protocol:
            source = step.source
            volume_ul = step.volume
            if float(volume_ul) <= 0:
                continue
            stock_name = self.config.get("deck", {}).get(source)
            if stock_name is None:
                raise ValueError(f"No stock name found for deck location: {source}")

            transfer_params, selected_tip_location = self._build_stock_transfer_params(
                stock_name=stock_name,
                volume_ul=volume_ul,
                step_tip_location=getattr(step, "tip_location", None),
            )
            try:
                transfer_result = self.transfer(
                    source=source,
                    dest=destination,
                    volume=volume_ul,
                    **transfer_params,
                )
                self._record_prepare_transfer(
                    stage_type="single",
                    source=source,
                    dest=destination,
                    requested_volume_ul=float(volume_ul),
                    source_stock_name=stock_name,
                    transfer_params=transfer_params,
                    transfer_result=transfer_result,
                    planned_transfer={
                        "source": source,
                        "dest": destination,
                        "source_stock_name": stock_name,
                    },
                )
                self._activate_stock_tip_reservation(stock_name, selected_tip_location)
            except Exception as e:
                warnings.warn(f"Transfer failed from {source} to {destination}: {str(e)}", stacklevel=2)
                return False

        self.last_target_location = destination
        self._mark_sample_locations_occupied([destination])
        return True

    def _resolve_stage_source(self, source_location, intermediate_map):
        """Resolve a staged source token to a concrete deck location.

        Parameters
        ----------
        source_location : str
            Source location or ``@intermediate:<id>`` token.
        intermediate_map : dict
            Mapping from intermediate identifiers to deck locations.

        Returns
        -------
        str
            Concrete deck location.

        Raises
        ------
        ValueError
            If an intermediate token cannot be resolved.
        """
        if isinstance(source_location, str) and source_location.startswith("@intermediate:"):
            key = source_location.split(":", 1)[1]
            if key not in intermediate_map:
                raise ValueError(f"Unknown intermediate source token: {source_location}")
            return intermediate_map[key]
        return source_location

    def _record_prepare_transfer(
        self,
        stage_type,
        source,
        dest,
        requested_volume_ul,
        source_stock_name,
        transfer_params,
        transfer_result,
        planned_transfer=None,
        extra=None,
    ):
        """Append a structured preparation transfer record.

        Parameters
        ----------
        stage_type : str
            Preparation stage label such as ``"single"`` or ``"final_mix"``.
        source, dest : str
            Source and destination deck locations.
        requested_volume_ul : float
            Requested transfer volume in microliters.
        source_stock_name : str or None
            Stock name associated with the source location.
        transfer_params : dict
            Transfer keyword arguments used for execution.
        transfer_result : dict
            Result returned by :meth:`transfer`.
        planned_transfer : dict, optional
            Original planned transfer metadata.
        extra : dict, optional
            Additional fields merged into the stored record.
        """
        entry = {
            "stage_type": stage_type,
            "source_location": source,
            "dest_location": dest,
            "source_stock_name": source_stock_name,
            "requested_volume_ul": float(requested_volume_ul),
            "transfer_params": dict(transfer_params or {}),
            "transfer_result": transfer_result,
        }
        if planned_transfer is not None:
            entry["planned_transfer"] = planned_transfer
        if extra:
            entry.update(extra)
        self._append_prepare_transfer(entry)

    def _transfer_stage(
        self,
        source,
        dest,
        volume_ul,
        stage_type,
        source_stock_name=None,
        planned_transfer=None,
        extra=None,
    ):
        """Execute and record one staged preparation transfer.

        Parameters
        ----------
        source, dest : str
            Source and destination deck locations.
        volume_ul : float
            Transfer volume in microliters.
        stage_type : str
            Stage label used in transfer bookkeeping.
        source_stock_name : str, optional
            Stock name associated with the source.
        planned_transfer : dict, optional
            Planned transfer metadata.
        extra : dict, optional
            Additional bookkeeping fields.
        """
        if float(volume_ul) <= 0:
            return
        stock_name = source_stock_name
        if stock_name is None:
            stock_name = self.config.get("deck", {}).get(source)
        selected_tip_location = None
        if stock_name is not None:
            transfer_params, selected_tip_location = self._build_stock_transfer_params(
                stock_name=stock_name,
                volume_ul=volume_ul,
            )
        else:
            transfer_params = self.get_transfer_params("default")
        transfer_result = self.transfer(source=source, dest=dest, volume=volume_ul, **transfer_params)
        self._activate_stock_tip_reservation(stock_name, selected_tip_location)
        self._record_prepare_transfer(
            stage_type=stage_type,
            source=source,
            dest=dest,
            requested_volume_ul=float(volume_ul),
            source_stock_name=stock_name,
            transfer_params=transfer_params,
            transfer_result=transfer_result,
            planned_transfer=planned_transfer,
            extra=extra,
        )

    def execute_preparation_plan(self, target, balanced_target, destination, procedure_plan, intermediate_destinations):
        """Execute a staged preparation plan with intermediates.

        Parameters
        ----------
        target : object
            Original target specification.
        balanced_target : object
            Balanced target object associated with the plan.
        destination : str
            Final destination location.
        procedure_plan : dict
            Staged plan containing dilution and final-mix steps.
        intermediate_destinations : sequence of str
            Concrete deck locations assigned to intermediate stages.

        Returns
        -------
        bool
            ``True`` when the plan completes successfully.

        Raises
        ------
        ValueError
            If the intermediate mapping is inconsistent or a stage type is
            unknown.

        Examples
        --------
        >>> driver.execute_pre preparation_plan(target, balanced_target, "4A1", plan, ["5A1"])
        True
        """
        intermediate_ids = procedure_plan.get("intermediate_ids", [])
        if len(intermediate_ids) != len(intermediate_destinations):
            raise ValueError(
                f"Intermediate destination mismatch. Need {len(intermediate_ids)}, got {len(intermediate_destinations)}."
            )
        intermediate_map = {
            intermediate_id: intermediate_destinations[i]
            for i, intermediate_id in enumerate(intermediate_ids)
        }

        stages = procedure_plan.get("stages", [])
        for stage in stages:
            stage_type = stage.get("stage_type")
            if stage_type == "dilution":
                dest_token = stage.get("destination_token")
                if not isinstance(dest_token, str) or not dest_token.startswith("@intermediate:"):
                    raise ValueError(f"Invalid dilution destination token: {dest_token}")
                intermediate_id = dest_token.split(":", 1)[1]
                if intermediate_id not in intermediate_map:
                    raise ValueError(f"No destination assigned for intermediate '{intermediate_id}'")
                stage_dest = intermediate_map[intermediate_id]

                source_loc = self._resolve_stage_source(stage.get("source_location"), intermediate_map)
                diluent_loc = self._resolve_stage_source(stage.get("diluent_location"), intermediate_map)
                source_mass_g = float(stage.get("total_source_mass_g", 0.0))
                diluent_mass_g = float(stage.get("total_diluent_mass_g", 0.0))
                if source_mass_g > 0:
                    source_stock = self.stocks_by_location(source_loc)
                    source_volume = source_stock.measure_out(f"{source_mass_g} g").volume.to("ul").magnitude
                    self._transfer_stage(
                        source_loc,
                        stage_dest,
                        source_volume,
                        stage_type="dilution",
                        source_stock_name=stage.get("source_stock_name"),
                        planned_transfer={
                            "required_mass_g": source_mass_g,
                            "source_location": source_loc,
                            "destination_token": dest_token,
                        },
                        extra={
                            "intermediate_id": intermediate_id,
                            "destination_token": dest_token,
                            "dilution_factor": stage.get("dilution_factor"),
                            "batches": stage.get("batches"),
                            "transfer_role": "source",
                            "intermediate_location": stage_dest,
                        },
                    )
                if diluent_mass_g > 0:
                    diluent_stock = self.stocks_by_location(diluent_loc)
                    diluent_volume = diluent_stock.measure_out(f"{diluent_mass_g} g").volume.to("ul").magnitude
                    self._transfer_stage(
                        diluent_loc,
                        stage_dest,
                        diluent_volume,
                        stage_type="dilution",
                        source_stock_name=stage.get("diluent_stock_name"),
                        planned_transfer={
                            "required_mass_g": diluent_mass_g,
                            "source_location": diluent_loc,
                            "destination_token": dest_token,
                        },
                        extra={
                            "intermediate_id": intermediate_id,
                            "destination_token": dest_token,
                            "dilution_factor": stage.get("dilution_factor"),
                            "batches": stage.get("batches"),
                            "transfer_role": "diluent",
                            "intermediate_location": stage_dest,
                        },
                    )
            elif stage_type == "final_mix":
                for transfer in stage.get("transfers", []):
                    source_loc = self._resolve_stage_source(transfer.get("source_location"), intermediate_map)
                    vol_ul = float(transfer.get("required_volume_ul", 0.0))
                    if vol_ul <= 0:
                        continue
                    self._transfer_stage(
                        source_loc,
                        destination,
                        vol_ul,
                        stage_type="final_mix",
                        source_stock_name=transfer.get("source_stock_name"),
                        planned_transfer=transfer,
                        extra={
                            "destination_location": destination,
                        },
                    )
            else:
                raise ValueError(f"Unknown stage type '{stage_type}' in procedure plan")

        self.last_target_location = destination
        self._mark_sample_locations_occupied(list(intermediate_destinations) + [destination])
        return True

    def stocks_by_location(self, location):
        """Return the configured stock object at a deck location.

        Parameters
        ----------
        location : str
            Deck location associated with a configured stock.

        Returns
        -------
        object
            Matching stock object.

        Raises
        ------
        ValueError
            If no stock is configured at the requested location.
        """
        for stock in self.stocks:
            if stock.location == location:
                return stock
        raise ValueError(f"No stock configured at location '{location}'")

    def build_prepare_result(self, feasible_result, balanced_target):
        """Build the serialized result payload for a preparation.

        Parameters
        ----------
        feasible_result : object
            Feasibility result from the preparation workflow.
        balanced_target : object
            Balanced target object to serialize.

        Returns
        -------
        dict
            Serialized target data with total volume included when available.
        """
        result_dict = balanced_target.to_dict()
        if hasattr(balanced_target, "volume") and balanced_target.volume is not None:
            result_dict["total_volume"] = f"{balanced_target.volume.to('ul').magnitude} ul"
        return result_dict

    def process_stocks(self):
        """Process stocks and refresh deck-derived preparation state.

        Notes
        -----
        This extends :class:`PrepareDriver` stock processing by rebuilding the
        reverse deck map and stock-tip reservation state.
        """
        PrepareDriver.process_stocks(self)
        self._update_deck_config()
        self._sync_stock_tip_tracking()

    def _update_deck_config(self):
        """Rebuild the reverse deck map from stock locations.

        Examples
        --------
        >>> driver._update_deck_config()
        >>> isinstance(driver.config.get("deck", {}), dict)
        True
        """
        deck_config = {}
        stock_locations = self.config.get("stock_locations", {})
        for stock_name, deck_location in stock_locations.items():
            deck_config[deck_location] = stock_name
        self.config["deck"] = deck_config

    def get_transfer_params(self, stock_name):
        """Return merged transfer parameters for a stock.

        Parameters
        ----------
        stock_name : str
            Stock identifier.

        Returns
        -------
        dict
            Default transfer parameters overlaid with stock-specific overrides.

        Examples
        --------
        >>> isinstance(driver.get_transfer_params("default"), dict)
        True
        """
        stock_params = self.config.get("stock_transfer_params", {}).get(stock_name, {})
        default_params = self.config.get("stock_transfer_params", {}).get("default", {})
        params = default_params.copy()
        params.update(stock_params)
        return params

    def reorder_protocol(self, protocol):
        """Reorder protocol steps according to configured stock order.

        Parameters
        ----------
        protocol : sequence
            Protocol steps with a ``source`` attribute.

        Returns
        -------
        list
            Reordered protocol steps.
        """
        stock_mix_order = self.config.get("stock_mix_order", [])
        if not stock_mix_order:
            return protocol

        steps_by_source = {}
        for step in protocol:
            if step.source not in steps_by_source:
                steps_by_source[step.source] = []
            steps_by_source[step.source].append(step)

        reordered = []
        for stock_name in stock_mix_order:
            if stock_name in steps_by_source:
                reordered.extend(steps_by_source[stock_name])
                del steps_by_source[stock_name]

        for steps in steps_by_source.values():
            reordered.extend(steps)
        return reordered

    def transfer_to_catch(self, source=None, dest=None, **kwargs):
        """Transfer a prepared sample into the configured catch destination.

        Parameters
        ----------
        source : str, optional
            Source location. Defaults to the last preparation destination.
        dest : str, optional
            Destination override for the catch transfer.
        **kwargs
            Additional transfer keyword arguments merged into the configured
            catch protocol.

        Returns
        -------
        None
            The method raises on failure and records the transfer on success.

        Raises
        ------
        ValueError
            If no source or destination can be resolved.
        RuntimeError
            If the underlying transfer fails.
        """
        catch_params = self.config.get("catch_protocol", {}).copy()
        if source is None:
            if self.last_target_location is None:
                raise ValueError(
                    "No source specified and no last target location available. "
                    "Call prepare() first or specify source."
                )
            source = self.last_target_location
        kwargs["source"] = source

        if dest is not None:
            kwargs["dest"] = dest

        catch_params.update(kwargs)
        if "dest" not in catch_params:
            raise ValueError("Destination 'dest' must be specified in catch_protocol config or as an argument.")

        try:
            transfer_result = self.transfer(**catch_params)
            self._record_prepare_transfer(
                stage_type="catch",
                source=catch_params["source"],
                dest=catch_params["dest"],
                requested_volume_ul=float(catch_params.get("volume", 0.0)),
                source_stock_name=self.config.get("deck", {}).get(catch_params["source"]),
                transfer_params={k: v for k, v in catch_params.items() if k not in ("source", "dest", "volume")},
                transfer_result=transfer_result,
            )
        except Exception as e:
            dest_val = catch_params.get("dest", "unknown")
            warnings.warn(
                f"Transfer to catch failed from {source} to {dest_val} using {catch_params}: {str(e)}",
                stacklevel=2,
            )
            raise

    def load_gen1_p10(self, mount, tip_rack_slots, **kwargs):
        """Load a GEN1 P10 single-channel pipette.

        Parameters
        ----------
        mount : {"left", "right"}
            Mount on which to load the pipette.
        tip_rack_slots : sequence of str
            Tiprack slots associated with the pipette.
        **kwargs
            Additional keyword arguments forwarded to :meth:`load_instrument`.

        Returns
        -------
        str
            Loaded pipette identifier returned by the robot.
        """
        return self.load_instrument(
            name="p10_single",
            mount=mount,
            tip_rack_slots=tip_rack_slots,
            **kwargs,
        )

    def reset(self):
        """Reset preparation targets and stock state.

        Notes
        -----
        This reset is preparation-focused and delegates to
        :class:`PrepareDriver` helpers rather than resetting the OT-2 run.
        """
        self.reset_targets()
        self.reset_stocks()


_DEFAULT_PORT = 5002
if __name__ == "__main__":
    from AFL.automation.shared.launcher import *
