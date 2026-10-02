"""Execute intake's capture and decisions campaign in emitted hosts."""

import pytest

from tests.composed_host import run_composed_tests


@pytest.mark.parametrize(
    "case",
    (
        "IntakeCampaign",
        "IntakeDenormalizedCampaign",
        "CaptureCampaign",
        "CaptureDenormalizedCampaign",
    ),
)
def test_intake_capture_and_access_decision_contracts(tmp_path, case):
    run_composed_tests(tmp_path, f"tests.native_intake_campaign.{case}", app="angee.intake")
