"""Canonical protected MTI capture through the native composed-host fixture."""

from angee.base.testing.packages import write_addon_distribution
from tests.composed_host import run_composed_tests


def test_canonical_protected_child_retains_native_composed_history(tmp_path):
    addon = tmp_path / "addons" / "mti_history"
    addon.mkdir(parents=True)
    (addon / "__init__.py").write_text("")
    write_addon_distribution(addon, "mti_history", depends_on=("angee.provenance", "angee.iam"))
    (addon / "models.py").write_text("""
from django.db import models
from angee.iam.models.base import ProtectedDataModel, ProtectedModel
from angee.iam.models.ownership import OwnerMixin
from angee.provenance.contrib.angee.history import HistoryMixin
from angee.provenance.contrib.angee.managers import AngeeHistoryManager, AngeeHistoryUnscopedManager
from angee.provenance.contrib.angee.querysets import AngeeHistoryQuerySet

class ParentQuerySet(AngeeHistoryQuerySet):
    def named(self, title): return self.filter(title=title)
class ParentManager(AngeeHistoryManager.from_queryset(ParentQuerySet)):
    def __init__(self, marker):
        super().__init__()
        self.marker = marker
    def factory_marker(self): return self.marker
class ChildManager(AngeeHistoryManager):
    def __init__(self, marker):
        super().__init__()
        self.marker = marker
    def factory_marker(self): return self.marker

class Parent(OwnerMixin, HistoryMixin, ProtectedDataModel, concrete=True):
    title = models.CharField(max_length=80)
    sqid_prefix = 'mhp_'
    objects = ParentManager('parent')
    unscoped_objects = AngeeHistoryUnscopedManager()
    class Meta:
        abstract = True
        base_manager_name = 'unscoped_objects'
        rebac_resource_type = 'mti_history/parent'

class Child(HistoryMixin, ProtectedModel, inherits='mti_history.Parent'):
    child_key = models.IntegerField(primary_key=True)
    detail = models.CharField(max_length=80, default='')
    objects = ChildManager('child')
    class Meta:
        abstract = True
        base_manager_name = '_angee_base'
        rebac_resource_type = 'mti_history/child'
""")
    (addon / "permissions.zed").write_text("""
// @rebac_package: mti_history
// @rebac_schema_revision: 1
definition mti_history/parent {
    relation owner: auth/user // rebac:field=owner
    permission create = authenticated
    permission read = owner
    permission write = owner
    permission transfer = owner
    permission write__owner = transfer
}
definition mti_history/child {
    relation parent: mti_history/parent // rebac:field=parent_ptr
    permission create = parent->write
    permission read = parent->read
    permission write = parent->write
    permission write__owner = parent->transfer
}
""")
    run_composed_tests(
        tmp_path,
        "tests.native_provenance_mti.CanonicalProtectedHistoryMTITests",
        app="mti_history",
        addon_dirs=(tmp_path / "addons",),
    )
