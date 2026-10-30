"""Angee's lightweight metadata and path seams do not load model/build owners."""

from __future__ import annotations

import os
import subprocess
import sys

from tests.composed_host import COMPOSED_TEST_TIMEOUT


def test_package_reader_is_safe_before_settings_and_model_loading() -> None:
    """Native provider metadata is readable before Django phase1."""

    script = "\n".join((
        "import sys",
        "from django.conf import settings",
        "from angee.base.addons import addon_facts",
        "assert addon_facts('django.contrib.contenttypes', 'angee.platform') == {}",
        "assert not settings.configured",
        "assert 'rebac.mixins' not in sys.modules",
        "assert not any(name.startswith('angee.base.models') for name in sys.modules)",
    ))
    result = subprocess.run(
        [sys.executable, "-c", script],
        env={key: value for key, value in os.environ.items() if key != "DJANGO_SETTINGS_MODULE"},
        capture_output=True, text=True, timeout=COMPOSED_TEST_TIMEOUT,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_shared_base_utilities_import_without_models_or_build_engine() -> None:
    """Base infrastructure composes native utilities below model compilation."""

    script = "\n".join((
        "import sys",
        "import angee.base.paths, angee.base.testing.packages",
        "blocked = {'django_compose.build', 'django_compose.model_composition', 'django_compose.rendering'}",
        "assert not blocked.intersection(sys.modules)",
        "assert 'django.db.models' not in sys.modules",
        "assert 'angee.base.models' not in sys.modules",
    ))
    result = subprocess.run(
        [sys.executable, "-c", script],
        env={key: value for key, value in os.environ.items() if key != "DJANGO_SETTINGS_MODULE"},
        capture_output=True, text=True, timeout=COMPOSED_TEST_TIMEOUT,
    )
    assert result.returncode == 0, result.stdout + result.stderr
