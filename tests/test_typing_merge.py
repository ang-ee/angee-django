"""Framework merge policy declarations keep the owner's static type for consumer donors."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_DECLARING_SOURCES = {
    "angee.messaging.models": ("Message", "Recipient"),
    "angee.nexus.models": ("Tie",),
    "angee.portfolio.models": ("Product",),
    "angee.posts.models": ("Feed",),
    "angee.projects.models": ("Project", "Task"),
}


def test_declared_merge_policies_compose_beside_an_owner_typed_base(tmp_path: Path) -> None:
    """mypy requires equal types for a writable attribute two bases declare; an inferred dict differs."""

    root = Path(__file__).resolve().parents[1]
    donors = tmp_path / "merge_policy_donors.py"
    donors.write_text(
        "from collections.abc import Mapping\n"
        "from typing import Any, ClassVar\n"
        "from angee.base.models.merge_references import MergeContractMixin, MergePolicy\n"
        "class Scoped(MergeContractMixin):\n"
        "    merge_policies: ClassVar[Mapping[str, Any]] = {'company': MergePolicy.BLOCK}\n"
        "    class Meta:\n"
        "        abstract = True\n"
        "class Inferred(MergeContractMixin):\n"
        "    merge_policies = {'owner': MergePolicy.KEEP}\n"
        "    class Meta:\n"
        "        abstract = True\n"
        "class InferredDonor(Scoped, Inferred):\n"
        "    class Meta:\n"
        "        abstract = True\n"
        + "".join(
            f"from {module} import {name}\n"
            f"class {name}Donor(Scoped, {name}):\n"
            "    class Meta:\n"
            "        abstract = True\n"
            for module, names in _DECLARING_SOURCES.items()
            for name in names
        )
    )
    env = dict(os.environ)
    env.pop("ANGEE_PROJECT_DIR", None)
    env["PYTHONPATH"] = os.pathsep.join((str(root), str(root / "src")))
    result = subprocess.run([
        sys.executable, "-m", "mypy", "--config-file", str(root / "pyproject.toml"),
        "--cache-dir", str(tmp_path / "cache"), str(donors),
    ], cwd=root, env=env, capture_output=True, text=True, check=False)
    assert result.returncode == 1, result.stdout + result.stderr
    assert result.stdout.count(": error:") == 1, result.stdout
    assert 'merge_policy_donors.py:12: error: Definition of "merge_policies" in base class "Scoped"' in result.stdout
