"""Import and phase-order boundaries of the reusable Django compiler."""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

from tests.composed_host import COMPOSED_TEST_TIMEOUT


def test_manifest_adapter_is_safe_before_settings_and_model_loading() -> None:
    """Host app ordering imports no permission models before Django phase 1."""

    script = "\n".join((
        "import sys",
        "from django.conf import settings",
        "from angee.conf import ManifestDeclarations",
        "from angee.compose.appgraph import AppGraph",
        "configs = AppGraph(ManifestDeclarations()).resolve(('django.contrib.contenttypes',))",
        "assert len(configs) == 1",
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


def test_generic_composer_imports_without_host_or_addon_policy() -> None:
    """The compiler's public modules remain importable outside an Angee host."""

    script = "\n".join((
        "import sys",
        "import angee.compose.appgraph, angee.compose.composer, angee.compose.migrations",
        "import angee.compose.model_composition, angee.compose.runtime, angee.compose.typing",
        "from django.conf import settings",
        "assert not settings.configured",
        "for name in ('angee.addons', 'angee.conf', 'angee.base.build', 'angee.base.models', 'angee.provenance'):",
        "    assert not any(module == name or module.startswith(name + '.') for module in sys.modules), name",
    ))
    result = subprocess.run(
        [sys.executable, "-c", script],
        env={key: value for key, value in os.environ.items() if key != "DJANGO_SETTINGS_MODULE"},
        capture_output=True, text=True, timeout=COMPOSED_TEST_TIMEOUT,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("prefixes", [None, (), ("PROJECT_",)])
def test_generic_autoconfig_environment_prefixes_belong_to_the_host(tmp_path, prefixes):
    """App defaults accept only same-name environment values selected by the host."""

    app = tmp_path / "prefix_owner"
    app.mkdir()
    (app / "__init__.py").write_text("")
    (app / "autoconfig.py").write_text(
        "SETTINGS = {'PROJECT_VALUE': 'default', 'ANGEE_VALUE': 'default'}\n",
    )
    script = "\n".join((
        "import sys",
        f"sys.path.insert(0, {str(tmp_path)!r})",
        "from angee.compose.autoconfig import AutoConfig",
        "from django.conf import settings",
        "namespace = {'INSTALLED_APPS': ['prefix_owner']}",
        f"prefixes = {prefixes!r}",
        "if prefixes is not None: namespace['COMPOSE_ENV_PREFIXES'] = prefixes",
        "AutoConfig.apply_installed(namespace)",
        "assert namespace['PROJECT_VALUE'] == ('environment' if prefixes else 'default')",
        "assert namespace['ANGEE_VALUE'] == 'default'",
        "assert not settings.configured",
    ))
    result = subprocess.run(
        [sys.executable, "-c", script],
        env={
            **{key: value for key, value in os.environ.items() if key != "DJANGO_SETTINGS_MODULE"},
            "PROJECT_VALUE": "environment", "ANGEE_VALUE": "environment",
        },
        capture_output=True, text=True, timeout=COMPOSED_TEST_TIMEOUT,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_shared_artifact_utilities_import_without_models_or_composer() -> None:
    """Serving and build generators share infrastructure below both owners."""

    script = "\n".join((
        "import sys",
        "import angee.utils.artifacts, angee.utils.files, angee.utils.paths",
        "assert not any(name == 'angee.compose' or name.startswith('angee.compose.') for name in sys.modules)",
        "assert 'django.db.models' not in sys.modules",
        "assert 'angee.base.models' not in sys.modules",
    ))
    result = subprocess.run(
        [sys.executable, "-c", script],
        env={key: value for key, value in os.environ.items() if key != "DJANGO_SETTINGS_MODULE"},
        capture_output=True, text=True, timeout=COMPOSED_TEST_TIMEOUT,
    )
    assert result.returncode == 0, result.stdout + result.stderr
