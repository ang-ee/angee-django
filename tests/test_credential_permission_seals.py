"""Credential verbs keep their complete permission intersections after composition."""

from pathlib import Path

import pytest
from django.apps import apps
from django.core.checks.registry import registry
from django.core.management import call_command
from rebac import RelationshipTuple, to_object_ref, to_subject_ref, write_relationships

from angee.base.build import extension_source_map
from angee.base.permissions import declared_schema_path
from angee.base.testing.permissions import emit_permissions, installed_field_owners
from angee.iam.checks import check_set_password_permissions
from angee.integrate.checks import check_replace_secret_permissions
from tests.conftest import create_platform_admin, create_user
from tests.test_zed_extensions import _addon, _apply_emitted_schema_paths, _contrib_addon


@pytest.mark.parametrize(("label", "resource", "permission", "check", "error_id"), [
    ("iam", "auth/user", "set_password", check_set_password_permissions, "angee.iam.E003"),
    ("integrate", "integrate/credential", "replace_secret", check_replace_secret_permissions, "integrate.E001"),
])
def test_password_permission_intersections_are_sealed_and_registered(
    tmp_path, label, resource, permission, check, error_id,
):
    assert check() == []
    assert sum(candidate is check for candidate in registry.registered_checks) == 1
    path = declared_schema_path(apps.get_app_config(label))
    assert isinstance(path, Path)
    owner = _addon(tmp_path, label, "permissions.zed", path.read_text())
    contrib = _contrib_addon(tmp_path, f"definition {resource} {{\npermission {permission} = write\n}}\n",
                             name="credential_contrib")
    sources = extension_source_map([owner, contrib])
    runtime = tmp_path / "runtime"
    for relative, text in sources.items():
        output = runtime / relative
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text)
    _apply_emitted_schema_paths([owner, contrib], runtime, sources=sources)
    [error] = check([owner, contrib])
    assert error.id == error_id
    assert f"{resource}#{permission}" in error.msg
    assert "credential_contrib" in error.msg


def test_administer_and_write_fragment_arms_cannot_widen_set_password(db, composed_permissions, tmp_path):
    """The seal still caps the audience even when all extensible gates admit an outsider."""

    configs = list(apps.get_app_configs())
    contrib = _contrib_addon(tmp_path, """definition auth/user {
        permission administer = directory_reader
        permission write = directory_reader
        permission write__password = directory_reader
    }""", name="password_contrib")
    composed = [*configs, contrib]
    sources = extension_source_map(composed, field_owners=installed_field_owners(configs))
    emit_permissions(configs, tmp_path / "runtime", sources=sources)
    assert check_set_password_permissions(composed) == []
    call_command("rebac", "sync", "--force-overwrite", "--yes", verbosity=0)

    outsider = create_user("password-fragment-outsider")
    target = create_platform_admin("password-fragment-target")
    write_relationships([RelationshipTuple(to_object_ref(target), "directory_reader", to_subject_ref(outsider))])
    target.with_actor(outsider)
    assert all(target.has_access(action) for action in ("administer", "write", "write__password"))
    assert not target.has_access("set_password")
    assert "set_password" not in target.account_actions_for(outsider)
