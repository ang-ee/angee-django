"""Run grouped retrieval contracts with composed, actor-scoped models."""
import json
import os
import subprocess
import sys
from pathlib import Path

from django.db import connection

from tests.composed_host import COMPOSED_TEST_TIMEOUT
from angee.base.testing.packages import write_addon_distribution


def test_composed_knowledge_search(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    report = tmp_path / "knowledge-search.json"
    addon = tmp_path / "addons" / "example" / "retrieval_test"
    addon.mkdir(parents=True)
    (addon / "__init__.py").write_text("")
    write_addon_distribution(addon , 'example.retrieval_test', depends_on=('angee.knowledge',))
    (addon / "autoconfig.py").write_text(
        'SETTINGS = {"ANGEE_KNOWLEDGE_RETRIEVAL_CLASSES.reverse": "tests.knowledge_retrieval.ReverseRetrieval"}\n'
    )
    postgresql = connection.vendor == "postgresql"
    env = dict(os.environ)
    env.pop("DJANGO_SETTINGS_MODULE", None)
    result = subprocess.run(
        [
            sys.executable, str(root / "tests/composed_host.py"),
            "--runtime-dir", str(tmp_path / "runtime"), "--addon-dir", str(tmp_path / "addons"),
            "--app", "example.retrieval_test", "--no-examples",
            "--action", "tests", "--test-label", "tests.native_knowledge_search",
            "--output", str(report), *(["--test-postgresql"] if postgresql else []),
        ],
        cwd=root, env=env, capture_output=True, text=True, timeout=COMPOSED_TEST_TIMEOUT, check=False,
    )
    assert result.returncode == 0, f"composed knowledge search failed:\n{result.stdout}\n{result.stderr}"
    assert json.loads(report.read_text()) == {"failures": 0, "vendor": "postgresql" if postgresql else "sqlite"}
