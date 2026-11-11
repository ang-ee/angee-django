"""Run the project surface contracts in an emitted host carrying the complete work graph."""

from tests.composed_host import run_composed_tests


def test_project_surfaces_and_work_rules(tmp_path):
    run_composed_tests(tmp_path, "tests.native_projects_surface.ProjectSurfaceCampaign", app="angee.intake")
