from AFL.automation.prepare.PipetteAction import PipetteAction


def test_prepare_pipette_action_emits_tip_location():
    action = PipetteAction(
        source="1A1",
        dest="2A1",
        volume=50,
        tip_location="3A1",
    )

    assert action.tip_location == "3A1"
    assert action.get_kwargs()["tip_location"] == "3A1"
