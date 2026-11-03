"""The source addon must run without loading the former external distribution."""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap


def test_provenance_addon_does_not_import_former_distribution() -> None:
    script = textwrap.dedent("""
        import importlib.abc
        import os
        import sys

        former_package = "django" + "_provenance"

        class RejectFormerPackage(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                if fullname == former_package or fullname.startswith(former_package + "."):
                    raise AssertionError(f"Unexpected import: {fullname}")
                return None

        sys.meta_path.insert(0, RejectFormerPackage())
        os.environ["DJANGO_SETTINGS_MODULE"] = "tests.settings"
        import django
        django.setup()

        from angee.provenance import context, history, querysets, statements
        from angee.provenance.contrib.angee import history as protected_history
        from angee.provenance.engine import base, replay

        assert context.changeset is not None
        assert history.ModelHistory is not None
        assert querysets.HistoryQuerySet is not None
        assert statements.StatementRecords is not None
        assert protected_history.HistoryMixin is not None
        assert base.AbstractChangeset is not None
        assert replay.ordered_states is not None
        assert not any(name == former_package or name.startswith(former_package + ".")
                       for name in sys.modules)
    """)
    environment = os.environ.copy()
    environment.pop("DATABASE_URL", None)
    result = subprocess.run(
        [sys.executable, "-c", script],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
