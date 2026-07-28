from types import SimpleNamespace

import pytest

from AFL.automation.prepare.OT2Prepare import OT2Prepare


class DummyConfig(dict):
    def _update_history(self):
        return None


class StubOT2Prepare(OT2Prepare):
    def __init__(self):
        self.app = None
        self.data = {"prepare": {"executed_transfers": []}}
        self.config = DummyConfig({
            "deck": {"1A1": "Water"},
            "loaded_labware": {
                "1": ("tiprack-left", "opentrons_96_tiprack_300ul", {"definition": {"wells": {"A1": {}}}}),
                "2": ("tiprack-right", "opentrons_96_tiprack_20ul", {"definition": {"wells": {"A1": {}}}}),
            },
            "loaded_instruments": {
                "left": {"name": "p300_single", "tip_racks": ["tiprack-left"]},
                "right": {"name": "p20_single", "tip_racks": ["tiprack-right"]},
            },
            "available_tips": {
                "left": [("tiprack-left", "A1")],
                "right": [("tiprack-right", "A1")],
            },
            "stock_transfer_params": {
                "default": {"drop_tip": True},
                "Water": {"mix_after": [1, 10]},
            },
            "prep_targets": [],
            "occupied_sample_locations": [],
            "reserved_stock_tips": [],
            "stock_tip_locations": {},
            "stock_tip_reservations": {},
            "stocks": [],
            "stock_inventory": {},
            "stock_locations": {},
        })
        self.stocks = []
        self.targets = []
        self.last_target_location = None
        self.transfer_calls = []
        self.has_tip = False
        self.last_pipette = None
        self.current_tip = None
        self.session_id = None
        self.base_url = "http://example.invalid"
        self.headers = {}
        self.pipette_info = {}

    def get_pipette(self, volume):
        if float(volume) <= 20:
            return {"mount": "right", "name": "p20_single"}
        return {"mount": "left", "name": "p300_single"}

    def transfer(self, source, dest, volume, **kwargs):
        self.transfer_calls.append(
            {
                "source": source,
                "dest": dest,
                "requested_volume_ul": float(volume),
                "kwargs": dict(kwargs),
            }
        )
        return {
            "source": source,
            "dest": dest,
            "requested_volume_ul": float(volume),
            "subtransfers_ul": [float(volume)],
            "pipette_mount": "left",
            "pipette_name": "p300_single",
        }


def test_transfer_stage_records_prepare_execution_metadata():
    driver = StubOT2Prepare()

    driver._transfer_stage(
        source="1A1",
        dest="5A1",
        volume_ul=50.0,
        stage_type="single",
        source_stock_name="Water",
        planned_transfer={"required_volume_ul": 50.0},
        extra={"destination_location": "5A1"},
    )

    executed = driver.data["prepare"]["executed_transfers"]
    assert len(executed) == 1
    entry = executed[0]
    assert entry["stage_type"] == "single"
    assert entry["source_location"] == "1A1"
    assert entry["dest_location"] == "5A1"
    assert entry["source_stock_name"] == "Water"
    assert entry["requested_volume_ul"] == 50.0
    assert entry["transfer_params"]["mix_after"] == [1, 10]
    assert entry["transfer_result"]["subtransfers_ul"] == [50.0]
    assert entry["planned_transfer"]["required_volume_ul"] == 50.0


@pytest.mark.usefixtures("mixdb")
def test_process_stocks_tracks_reserved_stock_tips():
    driver = StubOT2Prepare()
    driver.config["stocks"] = [
        {"name": "Water", "masses": {"H2O": "20 g"}, "location": "2B1", "tip": ["1A1", "2A1"]},
        {"name": "Salt", "masses": {"H2O": "20 g"}, "location": "2B2"},
    ]

    driver.process_stocks()

    assert driver.config["deck"]["2B1"] == "Water"
    assert driver.config["deck"]["2B2"] == "Salt"
    assert driver.config["stock_tip_locations"] == {"Water": ["1A1", "2A1"]}
    assert driver.config["stock_tip_reservations"] == {}
    assert driver.config["reserved_stock_tips"] == []


@pytest.mark.usefixtures("mixdb")
def test_process_stocks_expands_multi_source_stock_and_preserves_shared_tip_locations():
    driver = StubOT2Prepare()
    driver.config["stocks"] = [
        {
            "name": "Water",
            "masses": {"H2O": "20 g"},
            "tip_location": ["1A1", "2A1"],
            "sources": [
                {"location": "2B1", "initial_volume": "500 ul"},
                {"location": "2B2", "initial_volume": "750 ul"},
            ],
        },
    ]

    driver.process_stocks()

    assert driver.config["deck"] == {"2B1": "Water", "2B2": "Water"}
    assert driver.config["stock_tip_locations"] == {"Water": ["1A1", "2A1"]}
    assert driver.config["stock_locations"] == {"Water": ["2B1", "2B2"]}
    assert [stock.location for stock in driver.stocks] == ["2B1", "2B2"]
    assert [stock.stock_id for stock in driver.stocks] == ["Water@2B1", "Water@2B2"]
    assert all(stock.tip_location == ["1A1", "2A1"] for stock in driver.stocks)


@pytest.mark.usefixtures("mixdb")
def test_status_reports_aggregate_stock_inventory_without_source_locations():
    driver = StubOT2Prepare()
    driver.config["stocks"] = [
        {
            "name": "Water",
            "masses": {"H2O": "20 g"},
            "sources": [
                {"location": "2B1", "initial_volume": "500 ul"},
                {"location": "2B2", "initial_volume": "750 ul"},
            ],
        },
        {"name": "Salt", "masses": {"H2O": "20 g"}, "location": "2B3", "total_volume": "1000 ul"},
    ]

    driver.process_stocks()

    status = driver.status()

    assert "Stock inventory remaining: {'Water': '1250.0 uL', 'Salt': '1000.0 uL'}" in status


def test_execute_preparation_activates_reservation_after_stock_tip_is_used():
    driver = StubOT2Prepare()
    driver.config["stocks"] = [
        {"name": "Water", "masses": {"H2O": "20 g"}, "location": "1A1", "tip": ["1A1", "2A1"]},
    ]
    driver.process_stocks()
    balanced_target = SimpleNamespace(
        protocol=[SimpleNamespace(source="1A1", volume=50.0, tip_location=["1A1", "2A1"])]
    )

    success = driver.execute_preparation({}, balanced_target, "5A1")

    assert success is True
    assert driver.transfer_calls[0]["kwargs"]["tip_location"] == "1A1"
    assert driver.config["stock_tip_reservations"] == {"Water": ["1A1"]}
    assert driver.config["reserved_stock_tips"] == ["1A1"]


def test_execute_preparation_marks_destination_occupied():
    driver = StubOT2Prepare()
    balanced_target = SimpleNamespace(protocol=[SimpleNamespace(source="1A1", volume=50.0)])

    success = driver.execute_preparation({}, balanced_target, "5A1")

    assert success is True
    assert driver.config["occupied_sample_locations"] == ["5A1"]


def test_execute_preparation_orders_stock_sources_by_stock_name_and_logs_actions(capsys):
    driver = StubOT2Prepare()
    driver.config["deck"] = {
        "1A1": "stock_Red",
        "1A2": "stock_Blue",
        "1A3": "stock_Red",
    }
    driver.config["stock_mix_order"] = ["stock_Blue", "stock_Red"]
    balanced_target = SimpleNamespace(
        protocol=[
            SimpleNamespace(source="1A1", volume=100.0),
            SimpleNamespace(source="1A2", volume=200.0),
            # A second Red source models an inventory-driven split transfer.
            SimpleNamespace(source="1A3", volume=50.0),
        ]
    )

    assert driver.execute_preparation({}, balanced_target, "5A1") is True

    assert [call["source"] for call in driver.transfer_calls] == ["1A2", "1A1", "1A3"]
    debug_output = capsys.readouterr().out
    assert debug_output.count("[DEBUG] Pipette action:") == 3
    assert "source='1A1'" in debug_output
    assert "source='1A3'" in debug_output


def test_resolve_destination_rejects_occupied_sample_location():
    driver = StubOT2Prepare()
    driver.config["prep_targets"] = ["5A1", "5A2"]
    driver.config["occupied_sample_locations"] = ["5A1"]

    with pytest.raises(ValueError, match="already contain a prepared sample"):
        driver.resolve_destination(None)

    assert driver.config["prep_targets"] == ["5A1", "5A2"]


def test_clear_sample_locations_allows_destination_reuse():
    driver = StubOT2Prepare()
    driver.config["occupied_sample_locations"] = ["5a1", "5A2"]

    cleared = driver.clear_sample_locations(["5A1"])

    assert cleared == ["5A1"]
    assert driver.config["occupied_sample_locations"] == ["5A2"]
    assert driver.resolve_destination("5A1") == "5A1"


def test_reset_clears_tip_reservations_and_occupied_samples():
    driver = StubOT2Prepare()
    driver.config["targets"] = [{"name": "Target"}]
    driver.config["stocks"] = [{"name": "Water", "location": "1A1"}]
    driver.config["stock_inventory"] = {"Water@1A1": {"remaining_volume": "1000 ul"}}
    driver.config["deck"] = {"1A1": "Water"}
    driver.config["occupied_sample_locations"] = ["5A1", "5A2"]
    driver.config["stock_tip_locations"] = {"Water": ["1A1", "2A1"]}
    driver.config["stock_tip_reservations"] = {"Water": ["1A1"]}
    driver.config["reserved_stock_tips"] = ["1A1"]
    driver.stocks = [SimpleNamespace(name="Water", location="1A1")]
    driver.targets = [SimpleNamespace(name="Target")]

    driver.reset()

    assert driver.config["targets"] == []
    assert driver.config["stocks"] == []
    assert driver.config["stock_inventory"] == {}
    assert driver.config["deck"] == {}
    assert driver.config["occupied_sample_locations"] == []
    assert driver.config["stock_tip_locations"] == {}
    assert driver.config["stock_tip_reservations"] == {}
    assert driver.config["reserved_stock_tips"] == []
    assert driver.stocks == []
    assert driver.targets == []


@pytest.mark.usefixtures("mixdb")
def test_prepare_stock_volume_fractions_emits_ot2_transfers():
    driver = StubOT2Prepare()
    driver.config["stocks"] = [
        {"name": "stock_Red", "masses": {"H2O": "20 g"}, "location": "1A1"},
        {"name": "stock_Blue", "masses": {"H2O": "20 g"}, "location": "1A2"},
        {"name": "stock_Green", "masses": {"H2O": "20 g"}, "location": "1A3"},
        {"name": "stock_Yellow", "masses": {"H2O": "20 g"}, "location": "1A4"},
    ]
    driver.process_stocks()
    driver.config["stock_mix_order"] = ["stock_Yellow", "stock_Blue", "stock_Red"]
    target = {
        "name": "color_sample",
        "location": "6A1",
        "stock_volume_fractions": {
            "stock_Red": 0.3,
            "stock_Blue": 0.4,
            "stock_Green": 0.1,
            "stock_Yellow": 0.2,
        },
        "total_volume": "1000 ul",
    }

    result, destination = driver.prepare(target=target, dest=target["location"])

    assert destination == "6A1"
    assert result["destination"] == "6A1"
    assert result["stock_transfer_volumes_ul"] == {
        "stock_Red": 300.0,
        "stock_Blue": 400.0,
        "stock_Green": 100.0,
        "stock_Yellow": 200.0,
    }
    assert [call["source"] for call in driver.transfer_calls] == ["1A4", "1A2", "1A1", "1A3"]
    assert [call["dest"] for call in driver.transfer_calls] == ["6A1", "6A1", "6A1", "6A1"]
    assert [call["requested_volume_ul"] for call in driver.transfer_calls] == [200.0, 400.0, 300.0, 100.0]


@pytest.mark.usefixtures("mixdb")
def test_prepare_stock_volume_fractions_splits_across_multiple_sources_and_tracks_inventory():
    driver = StubOT2Prepare()
    driver.config["stocks"] = [
        {
            "name": "stock_Red",
            "masses": {"H2O": "20 g"},
            "tip_location": ["1A1"],
            "sources": [
                {"location": "1A1", "initial_volume": "700 ul"},
                {"location": "1A2", "initial_volume": "1000 ul"},
            ],
        },
        {"name": "stock_Blue", "masses": {"H2O": "20 g"}, "location": "1A3", "total_volume": "1000 ul"},
    ]
    driver.process_stocks()

    result, destination = driver.prepare(
        target={
            "name": "split_sample",
            "location": "6A1",
            "stock_volume_fractions": {
                "stock_Red": 0.8,
                "stock_Blue": 0.2,
            },
            "total_volume": "1500 ul",
        },
        dest="6A1",
    )

    assert destination == "6A1"
    assert [call["source"] for call in driver.transfer_calls] == ["1A1", "1A2", "1A3"]
    assert [call["requested_volume_ul"] for call in driver.transfer_calls] == [700.0, 500.0, 300.0]
    assert driver.transfer_calls[0]["kwargs"]["tip_location"] == "1A1"
    assert driver.transfer_calls[1]["kwargs"]["tip_location"] == "1A1"
    assert result["stock_transfer_volumes_ul"] == {"stock_Red": 1200.0, "stock_Blue": 300.0}
    assert result["stock_inventory_after"]["stock_Red"]["sources"] == [
        {"stock_id": "stock_Red@1A1", "location": "1A1", "remaining_volume_ul": 0.0, "tip_location": "1A1"},
        {"stock_id": "stock_Red@1A2", "location": "1A2", "remaining_volume_ul": 500.0, "tip_location": "1A1"},
    ]
    assert driver.get_stock_inventory()["stock_Red"]["remaining_volume_ul"] == pytest.approx(500.0)
    executed = driver.data["prepare"]["executed_transfers"]
    assert executed[0]["remaining_before_ul"] == pytest.approx(700.0)
    assert executed[0]["remaining_after_ul"] == pytest.approx(0.0)
    assert executed[1]["remaining_before_ul"] == pytest.approx(1000.0)
    assert executed[1]["remaining_after_ul"] == pytest.approx(500.0)


@pytest.mark.usefixtures("mixdb")
def test_is_feasible_accepts_stock_volume_fractions_when_large_transfers_can_split():
    driver = StubOT2Prepare()
    driver.config["stocks"] = [
        {"name": "stock_Red", "masses": {"H2O": "20 g"}, "location": "1A1"},
        {"name": "stock_Blue", "masses": {"H2O": "20 g"}, "location": "1A2"},
    ]
    driver.process_stocks()
    target = {
        "name": "split_sample",
        "location": "6A1",
        "stock_volume_fractions": {
            "stock_Red": 0.7,
            "stock_Blue": 0.3,
        },
        "total_volume": "1000 ul",
    }

    feasible = driver.is_feasible(target)

    assert feasible == [{
        "name": "split_sample",
        "location": "6A1",
        "total_volume": "1000.0 ul",
        "stock_volume_fractions": {"stock_Red": 0.7, "stock_Blue": 0.3},
        "stock_transfer_volumes_ul": {"stock_Red": 700.0, "stock_Blue": 300.0},
    }]


@pytest.mark.usefixtures("mixdb")
def test_is_feasible_sets_tiny_stock_volume_fractions_to_zero_below_loaded_pipette_minimum():
    driver = StubOT2Prepare()

    def only_large_pipette(volume):
        if float(volume) < 20:
            raise ValueError("No suitable loaded pipettes found!")
        return {"mount": "left", "name": "p300_single"}

    driver.get_pipette = only_large_pipette
    driver.config["stocks"] = [
        {"name": "stock_Red", "masses": {"H2O": "20 g"}, "location": "1A1"},
        {"name": "stock_Blue", "masses": {"H2O": "20 g"}, "location": "1A2"},
    ]
    driver.process_stocks()
    target = {
        "name": "tiny_sample",
        "location": "6A1",
        "stock_volume_fractions": {
            "stock_Red": 0.005,
            "stock_Blue": 0.995,
        },
        "total_volume": "1000 ul",
    }

    feasible = driver.is_feasible(target)

    assert feasible[0]["stock_transfer_volumes_ul"] == {
        "stock_Red": 0.0,
        "stock_Blue": 995.0,
    }
    assert feasible[0]["total_volume"] == "995.0 ul"
    assert feasible[0]["stock_volume_fractions"]["stock_Red"] == pytest.approx(0.0)
    assert feasible[0]["stock_volume_fractions"]["stock_Blue"] == pytest.approx(1.0)


@pytest.mark.usefixtures("mixdb")
def test_is_feasible_skips_depleted_stock_sources_during_stock_processing():
    driver = StubOT2Prepare()
    driver.config["stocks"] = [
        {
            "name": "stock_Red",
            "concentrations": {"Red": "1 mg/ml"},
            "volumes": {"H2O": "20 ml"},
            "solutes": ["Red"],
            "sources": [{"location": "1A1", "initial_volume": "1000 ul"}],
        },
        {
            "name": "stock_Blue",
            "masses": {"H2O": "20 g"},
            "location": "1A2",
            "total_volume": "1000 ul",
        },
    ]
    driver.config["stock_inventory"] = {"stock_Red@1A1": {"remaining_volume": "0 ul"}}

    feasible = driver.is_feasible(
        {
            "name": "blue_only_sample",
            "location": "6A1",
            "stock_volume_fractions": {"stock_Blue": 1.0},
            "total_volume": "1000 ul",
        }
    )

    assert feasible == [{
        "name": "blue_only_sample",
        "location": "6A1",
        "total_volume": "1000.0 ul",
        "stock_volume_fractions": {"stock_Blue": 1.0},
        "stock_transfer_volumes_ul": {"stock_Blue": 1000.0},
    }]
    assert [stock.name for stock in driver.stocks] == ["stock_Blue"]


@pytest.mark.usefixtures("mixdb")
def test_prepare_sets_tiny_stock_volume_fraction_transfers_to_zero_below_loaded_pipette_minimum():
    driver = StubOT2Prepare()

    def only_large_pipette(volume):
        if float(volume) < 20:
            raise ValueError("No suitable loaded pipettes found!")
        return {"mount": "left", "name": "p300_single"}

    driver.get_pipette = only_large_pipette
    driver.config["stocks"] = [
        {"name": "stock_Red", "masses": {"H2O": "20 g"}, "location": "1A1"},
        {"name": "stock_Blue", "masses": {"H2O": "20 g"}, "location": "1A2"},
    ]
    driver.process_stocks()
    target = {
        "name": "tiny_sample",
        "location": "6A1",
        "stock_volume_fractions": {
            "stock_Red": 0.005,
            "stock_Blue": 0.995,
        },
        "total_volume": "1000 ul",
    }

    result, destination = driver.prepare(target=target, dest="6A1")

    assert destination == "6A1"
    assert result["stock_transfer_volumes_ul"] == {
        "stock_Red": 0.0,
        "stock_Blue": 995.0,
    }
    assert result["total_volume"] == "995.0 ul"
    assert result["stock_volume_fractions"]["stock_Red"] == pytest.approx(0.0)
    assert result["stock_volume_fractions"]["stock_Blue"] == pytest.approx(1.0)
    assert [call["requested_volume_ul"] for call in driver.transfer_calls] == [995.0]
