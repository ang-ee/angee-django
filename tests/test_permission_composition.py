"""Every installed permission backing has a concrete source-test model.

Run this module alone so unrelated test collection cannot supply missing models.
"""

from django.core.management import call_command


def test_every_installed_backing_resolves_and_the_index_builds(composed_permissions: None) -> None:
    """Merged permissions build an index and pass the native REBAC checks."""

    del composed_permissions
    call_command("check", "--tag", "rebac", verbosity=0)
