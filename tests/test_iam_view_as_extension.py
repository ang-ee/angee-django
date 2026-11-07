"""IAM keeps account preview a platform-admin action in the composed schema."""

from pathlib import Path

import pytest
from django.core.checks.registry import registry

from angee.base.build import extension_source_map
from angee.iam.checks import check_view_as_admin_only
from tests.test_zed_extensions import _addon, _apply_emitted_schema_paths, _contrib_addon

_IAM = """
definition angee/role {
    relation member: auth/user
}

definition auth/user {
    relation admin: angee/role // rebac:const=admin
    relation directory_reader: auth/user

    permission view_as = admin->member
    permission list = directory_reader + admin->member
}
"""

_FRAGMENTS = {
    "unrelated": "definition auth/user {\n    relation helper: auth/user\n    permission list = helper\n}\n",
    "relation": "definition auth/user {\n    relation previewer: auth/user\n    permission view_as = previewer\n}\n",
    "permission": "definition auth/user {\n    permission view_as = list\n}\n",
}


@pytest.mark.parametrize("fragment", sorted(_FRAGMENTS))
def test_check_rejects_any_fragment_arm_on_view_as(tmp_path: Path, fragment: str) -> None:
    iam = _addon(tmp_path, "iam", "permissions.zed", _IAM)
    contrib = _contrib_addon(tmp_path, _FRAGMENTS[fragment], name="preview_contrib")
    assert check_view_as_admin_only([iam]) == []
    sources = extension_source_map([iam, contrib])
    runtime = tmp_path / "runtime"
    for relative, text in sources.items():
        output = runtime / relative
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text)
    _apply_emitted_schema_paths([iam, contrib], runtime, sources=sources)

    errors = check_view_as_admin_only([iam, contrib])
    if fragment == "unrelated":
        assert errors == []
    else:
        [error] = errors
        assert error.id == "angee.iam.E001"
        assert "auth/user#view_as reaches" in error.msg
        assert ("previewer" if fragment == "relation" else "list") in error.msg
        assert "preview_contrib" in error.msg


def test_a_missing_iam_declaration_is_a_check_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """IAM's own arm is never flagged against an empty allowed set; the check reports, never raises."""

    monkeypatch.setattr("angee.iam.checks.declared_schema_path", lambda config: None)
    [error] = check_view_as_admin_only()
    assert error.id == "angee.iam.E001"
    assert "angee.iam declares no permissions schema" in error.msg


def test_installed_composition_grants_preview_to_platform_admins_only() -> None:
    assert check_view_as_admin_only() == []


def test_view_as_check_is_registered_once_by_iam() -> None:
    assert sum(candidate is check_view_as_admin_only for candidate in registry.registered_checks) == 1
