"""Blank country buckets on emitted Party and Address models."""

from pathlib import Path

import pytest

from tests.composed_host import run_composed_tests


def test_composed_party_and_address_blank_choice_groups(tmp_path: Path, subtests: pytest.Subtests) -> None:
    run_composed_tests(
        tmp_path, subtests, app="tests.blankchoices",
        sqlite=("tests.native_blank_choice_groups.BlankCountryGroupsTests",),
    )
