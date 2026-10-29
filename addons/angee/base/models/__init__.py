"""Django-shaped model toolkit for Angee applications."""

# ruff: noqa: F401 -- this module is the public re-export surface.

from angee.base.lineage import concrete_child
from angee.base.managers import AngeeManager, AngeeUnscopedManager
from angee.base.models.append_only import (
    AppendOnlyBaseManager,
    AppendOnlyBaseQuerySet,
    AppendOnlyModel,
    AppendOnlyQuerySet,
)
from angee.base.models.audit import AuditMixin
from angee.base.models.base import (
    AngeeDataModel,
    AngeeModel,
    DirectRecordAccess,
    record_display_label,
    role_anchor,
)
from angee.base.models.derived import DerivedFrom
from angee.base.models.hierarchy import HierarchyMixin, HierarchyQuerySet
from angee.base.models.identity import SqidMixin, TimestampMixin, update_fields_with_auto_now
from angee.base.models.inputs import InputExtensionsMixin
from angee.base.models.locks import (
    ImmutableFieldsMixin,
    OptimisticLockMixin,
    RowLockMixin,
    StaleRevisionError,
    require_revision,
)
from angee.base.models.merge import MergeableMixin
from angee.base.models.merge_references import MergePolicy, references
from angee.base.models.ownership import (
    ItemOwnershipMixin,
    OwnerMixin,
    OwnerQuerySet,
)
from angee.base.models.refs import RecordRefMixin
from angee.base.models.stages import Stage, StagedModelMixin
from angee.base.models.transitions import (
    StateTransitions,
    TransitionNotAllowed,
    get_transition_save_field,
    save_state,
    transition,
)
from angee.base.models.trash import ArchiveMixin, ArchiveQuerySet, TrashMixin, TrashQuerySet
from angee.base.querysets import (
    AngeeQuerySet,
    AngeeUnscopedQuerySet,
    MergeableQuerySet,
    by_key,
    candidate_relationships,
    row_lock_paths,
)
