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
            "stock_locations": {},
        })
        self.stocks = []
        self.targets = []
        self.last_target_location = None
        self.transfer_calls = []
        self.has_tip = False
        self.last_pipette = None
        self.current_tip = None

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
