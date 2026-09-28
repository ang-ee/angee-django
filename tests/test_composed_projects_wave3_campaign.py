"""Keep full-host projection and rule tests executable while host checks are blocked."""

import pytest

from tests.composed_host import run_composed_tests


@pytest.mark.parametrize("case", ("ProjectSurfaceCampaign", "ProjectSurfaceDenormalizedCampaign"))
def test_project_surfaces_and_work_rules(tmp_path, case):
    run_composed_tests(tmp_path, f"tests.native_projects_wave3_campaign.{case}", app="angee.intake")
