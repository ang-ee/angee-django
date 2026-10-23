"""Every shipped addon's model classes build, whether or not this host installs the addon.

A class whose bases cannot be linearized (an ancestor listed before a class that
already composes it) fails at import, so a composed host that installs the addon
cannot load its models at all. The bare test host installs only some addons;
this imports the rest.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MODEL_MODULES = sorted(
    [f"angee.{path.parent.name}.models" for path in (ROOT / "addons" / "angee").glob("*/models.py")]
    + [
        f"{path.parent.parent.name}.{path.parent.name}.models"
        for path in (ROOT / "examples" / "addons").glob("*/*/models.py")
    ]
)


@pytest.mark.parametrize("module", MODEL_MODULES)
def test_addon_model_classes_build(module: str) -> None:
    try:
        importlib.import_module(module)
    except TypeError as error:
        pytest.fail(f"{module}: {error}")
    except RuntimeError as error:
        # An addon the host does not install declares concrete models Django cannot
        # place; its classes were built (bases linearized) before that refusal.
        if "INSTALLED_APPS" not in str(error):
            raise
