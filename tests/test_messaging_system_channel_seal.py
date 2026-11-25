"""Administrators alone read, send on, write, annotate and delete the system channel, and no fragment unseals it."""

from __future__ import annotations

from pathlib import Path

import pytest
from django.apps import apps

from angee.base.build import extension_source_map
from angee.messaging.checks import check_derived_reads
from tests.test_zed_extensions import _addon, _apply_emitted_schema_paths, _contrib_addon

# A fragment arm on each sealed channel permission would reach the system channel past its
# seal. Fragments widen the parent integration instead, which the seal subtracts: the
# installed spaces and projects fragments do, and the installed composition passes.
_SEALED_CHANNEL_PERMISSIONS = ("read", "reply", "write", "annotate", "delete")


def test_the_installed_fragments_widen_the_integration_and_keep_the_seal() -> None:
    assert check_derived_reads() == []


@pytest.mark.parametrize("permission", _SEALED_CHANNEL_PERMISSIONS)
def test_check_refuses_fragment_arms_on_the_sealed_channel_permissions(tmp_path: Path, permission: str) -> None:
    declaration = Path(apps.get_app_config("messaging").path, "permissions.zed").read_text(encoding="utf-8")
    messaging = _addon(tmp_path, "messaging", "permissions.zed", declaration)
    contrib = _contrib_addon(
        tmp_path,
        f"definition messaging/channel {{\n    relation helper: auth/user\n    permission {permission} = helper\n}}\n",
        name="channel_contrib",
    )
    assert check_derived_reads([messaging]) == []
    sources = extension_source_map([messaging, contrib])
    runtime = tmp_path / "runtime"
    for relative, text in sources.items():
        output = runtime / relative
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text)
    _apply_emitted_schema_paths([messaging, contrib], runtime, sources=sources)

    [error] = check_derived_reads([messaging, contrib])
    assert error.id == "messaging.E003"
    assert f"messaging/channel#{permission}" in error.msg
    assert "channel_contrib" in error.msg
