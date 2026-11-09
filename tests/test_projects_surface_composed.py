"""Run the project surface contracts in an emitted host carrying the complete work graph."""

import pytest

from tests.composed_host import run_composed_tests


@pytest.mark.parametrize("case", ("ProjectSurfaceCampaign", "ProjectSurfaceDenormalizedCampaign"))
def test_project_surfaces_and_work_rules(tmp_path, case):
    run_composed_tests(tmp_path, f"tests.native_projects_surface.{case}", app="angee.intake")
