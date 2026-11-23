"""The example consumer's own tests, notes ownership and console budgets on a host composing every addon, and
the documents under ``REBAC_INHERIT_THROUGH_LINKS = False`` on such a host."""

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


def test_composed_documents_without_inheritance_through_links(tmp_path: Path, subtests: pytest.Subtests) -> None:
    """Every authored query and argument-free root, as readers who reach records only through links."""

    run_composed_tests(
        tmp_path, subtests, app=None,
        sqlite=("tests.native_inherit_through_links.InheritThroughLinksTests",),
        postgresql=("tests.native_inherit_through_links.InheritThroughLinksTests",),
    )
