"""The example consumer's own tests, notes ownership and console budgets on a host composing every addon."""

from pathlib import Path

import pytest

from tests.composed_host import run_composed_tests


def test_composed_examples(tmp_path: Path, subtests: pytest.Subtests) -> None:
    run_composed_tests(
        tmp_path, subtests, app=None,
        sqlite=(
            "example.notes.tests",
            "angee.projects.tests.test_query_budgets",
            "tests.notes_ownership_cases",
            "tests.native_notes_owner_campaign.NotesOwnerCampaign",
            "tests.native_graphql_document_budgets.ConsoleDocumentBudgetTests"
            ".test_console_reads_stay_within_their_fixed_ceilings",
        ),
        postgresql=(
            "example.notes.tests.test_workflow_steps",
            "tests.native_graphql_document_budgets.ConsoleDocumentBudgetTests.test_member_reads_planner_memory",
        ),
    )
