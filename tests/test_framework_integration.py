"""Collect integration joins in native composed SQLite and PostgreSQL hosts."""
from django.db import connection

from tests.composed_host import run_composed_tests
from tests.package_metadata import write_addon_distribution


def test_framework_integration(tmp_path):
    addon = tmp_path / "addons" / "example" / "memory_test"
    addon.mkdir(parents=True)
    (addon / "__init__.py").write_text("")
    write_addon_distribution(
        addon, "example.memory_test",
        depends_on=("angee.posts", "angee.knowledge", "angee.projects", "angee.mcp"),
    )
    (addon / "permissions.extends.zed").write_text(
        '// @rebac_package: memory_test\n// @rebac_schema_revision: 1\n'
        'definition knowledge/record_binding {\n'
        '  relation contact: parties/handle // rebac:field=target\n'
        '  permission target_read = contact->read\n'
        '  permission target_write = contact->write\n}\n'
    )
    run_composed_tests(tmp_path, "tests.native_framework_integration",
                       app="example.memory_test", addon_dirs=(tmp_path / "addons",),
                       test_postgresql=connection.vendor == "postgresql")
