"""IAM reserves account preview during additive permission composition."""

from pathlib import Path

import pytest
from django.apps import apps

from angee.iam.build import SchemaExtensionError, extension_source_map
from tests.conftest import make_addon


def test_fragment_cannot_extend_platform_admin_view_as(tmp_path: Path) -> None:
    fragment = make_addon(
        name="tests.preview_contrib",
        label="preview_contrib",
        path=tmp_path / "preview_contrib",
    )
    (Path(fragment.path) / "permissions.extends.zed").write_text("""
definition auth/user {
    relation previewer: auth/user
    permission view_as = previewer
}
""")
    with pytest.raises(
        SchemaExtensionError,
        match=r"tests\.preview_contrib: permissions\.extends\.zed cannot extend auth/user#view_as; "
        r"account preview is granted to platform admins only",
    ):
        extension_source_map([apps.get_app_config("iam"), fragment])
