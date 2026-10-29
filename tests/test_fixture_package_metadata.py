"""Source fixtures remain discoverable through native distribution metadata."""

from __future__ import annotations

import sys
from importlib import metadata

from django.apps import AppConfig
from django_compose.addons import installed_addons

from angee.base.testing.packages import write_addon_distribution


def test_fixture_distribution_preserves_native_identity_and_config(tmp_path, monkeypatch):
    package = tmp_path / "fixture_identity"
    package.mkdir()
    (package / "__init__.py").touch()
    write_addon_distribution(
        package, "fixture_identity", label="fixture_label",
        depends_on=("django.contrib.auth",), metadata_root=tmp_path,
    )
    distributions = tuple(metadata.distributions(path=[str(tmp_path)]))
    monkeypatch.setattr(metadata, "distributions", lambda **kwargs: distributions)
    monkeypatch.syspath_prepend(str(tmp_path))
    try:
        index = installed_addons("angee.addons")
        assert "fixture_identity" not in sys.modules
        assert index.providers["fixture_identity"] == "fixture-identity"
        assert index.active_requirements("fixture-identity") == ()
        config = AppConfig.create("fixture_identity")
        assert config.label == "fixture_label"
        assert config.requires_apps == ("django.contrib.auth",)
        assert AppConfig.create("fixture_identity").label == config.label
    finally:
        sys.modules.pop("fixture_identity.apps", None)
        sys.modules.pop("fixture_identity", None)
