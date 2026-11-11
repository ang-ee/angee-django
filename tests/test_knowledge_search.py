"""Run grouped retrieval contracts with composed, actor-scoped models."""
from pathlib import Path

import pytest

from angee.base.testing.packages import write_addon_distribution
from tests.composed_host import run_composed_tests


def test_composed_knowledge_search(tmp_path: Path, subtests: pytest.Subtests) -> None:
    addon = tmp_path / "addons" / "example" / "retrieval_test"
    addon.mkdir(parents=True)
    (addon / "__init__.py").write_text("")
    write_addon_distribution(addon , 'example.retrieval_test', depends_on=('angee.knowledge',))
    (addon / "autoconfig.py").write_text(
        'SETTINGS = {"ANGEE_KNOWLEDGE_RETRIEVAL_CLASSES.reverse": "tests.knowledge_retrieval.ReverseRetrieval"}\n'
    )
    labels = ("tests.native_knowledge_search",)
    run_composed_tests(
        tmp_path, subtests, app="example.retrieval_test", sqlite=labels, postgresql=labels,
        addon_dirs=(tmp_path / "addons",),
    )
