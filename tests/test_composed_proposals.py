"""Proposal clarifications, publication and the optional Work bridge, one composed host per profile."""

from pathlib import Path

import pytest

from tests.composed_host import run_composed_tests


@pytest.mark.parametrize("profile", [
    pytest.param({"app": "angee.proposals", "sqlite": (
        "tests.test_proposals_clarifications.ProposalsOnlyTests",
        "tests.test_proposals_publication.PublicationTests",
        "tests.test_proposals_record_verbs.RoundRecordTests.test_active_round_and_authored_documents",
    )}, id="proposals"),
    pytest.param({
        "app": ("angee.proposals", "angee.work"), "sqlite": ("tests.test_proposals_clarifications.WithWorkTests",),
    }, id="work"),
    pytest.param({
        "app": "angee.proposals_work", "sqlite": ("tests.test_proposals_work.ProposalsWorkTests",),
    }, id="bridge"),
])
def test_composed_proposals(tmp_path: Path, subtests: pytest.Subtests, profile: dict) -> None:
    run_composed_tests(tmp_path, subtests, **profile)
