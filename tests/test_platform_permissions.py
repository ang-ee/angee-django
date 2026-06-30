"""The REBAC-schema reconcile that keeps an addon uninstall clean.

When an addon leaves ``INSTALLED_APPS``, ``rebac sync`` never revisits it, so its
``Schema*`` rows orphan and the library's ``rebac.E009`` check then blocks every
checked command (``makemigrations``, ``migrate``, ``rebac sync``) — breaking the
rebuild the uninstall triggers. ``platform``'s ``reconcile_permission_schema`` (run
check-free by the ``reconcile_permissions`` command) is the global prune that removes
those orphans.
"""

from __future__ import annotations


def _managed(package: str, resource_type: str):
    """Create a SchemaDefinition with a PackageManagedRecord owning it, as sync would."""

    from django.contrib.contenttypes.models import ContentType
    from django.utils import timezone
    from rebac.models import PackageManagedRecord, SchemaDefinition

    definition = SchemaDefinition.objects.create(resource_type=resource_type)
    PackageManagedRecord.objects.create(
        package=package,
        external_id=f"definition:{resource_type}",
        schema_revision=1,
        target_ct=ContentType.objects.get_for_model(SchemaDefinition),
        target_pk=definition.pk,
        content_hash="x",
        last_synced_at=timezone.now(),
    )
    return definition


def test_reconcile_prunes_orphaned_package_and_keeps_composed(db) -> None:
    """A managed row whose package is not a composed app is pruned with its target,
    while a row for a composed app survives untouched."""

    from django.apps import apps
    from rebac.models import PackageManagedRecord, SchemaDefinition

    from angee.platform.permissions import reconcile_permission_schema

    orphan = _managed("ghost.addon", "ghost/thing")  # no such app in the composed set
    kept_package = apps.get_app_config("contenttypes").name  # a composed app
    kept = _managed(kept_package, "ghost/kept")

    assert reconcile_permission_schema() == 1

    assert not SchemaDefinition.objects.filter(pk=orphan.pk).exists()
    assert not PackageManagedRecord.objects.filter(package="ghost.addon").exists()
    assert SchemaDefinition.objects.filter(pk=kept.pk).exists()
    assert PackageManagedRecord.objects.filter(package=kept_package).exists()


def test_reconcile_is_a_noop_when_nothing_orphaned(db) -> None:
    """Every managed package composed (here: none managed at all) prunes nothing."""

    from angee.platform.permissions import reconcile_permission_schema

    assert reconcile_permission_schema() == 0
