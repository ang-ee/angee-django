"""Django-shaped model toolkit for Angee applications."""

# ruff: noqa: F401 -- this module is the public re-export surface.

from angee.base.errors import CreationKeyConflict
from angee.base.lineage import (
    concrete_child,
    concrete_child_accessor,
    concrete_child_models,
    concrete_lineage,
    most_derived_concrete,
)
from angee.base.models.append_only import (
    AppendOnlyBaseManager,
    AppendOnlyBaseQuerySet,
    AppendOnlyModel,
    AppendOnlyQuerySet,
)
from angee.base.models.base import (
    AngeeDataModel,
    AngeeModel,
    DerivedFrom,
    DirectRecordAccess,
    record_display_label,
    role_anchor,
)
from angee.base.models.hierarchy import HierarchyMixin, HierarchyQuerySet
from angee.base.models.history import ChangesetQuerySet, HistoryMixin, ModelHistory, ModelHistoryManager
from angee.base.models.identity import (
    SqidMixin,
    TimestampMixin,
    auto_now_stamps,
    update_fields_with_auto_now,
)
from angee.base.models.locks import (
    ImmutableFieldsMixin,
    OptimisticLockMixin,
    RowLockMixin,
    StaleRevisionError,
    require_revision,
    validate_revision,
)
from angee.base.models.manager import AngeeManager, AngeeUnscopedManager
from angee.base.models.merge import (
    MERGE_LIMIT,
    MERGE_REASON,
    EdgeReference,
    ForeignKeyReference,
    MergeableMixin,
    MergeContractMixin,
    MergePolicy,
    Reference,
    fold_records,
    named_in_relationships,
    reference_blockers,
    references,
    unique_field_sets,
)
from angee.base.models.ownership import (
    AuditMixin,
    ItemOwnershipMixin,
    OwnerMixin,
    OwnerQuerySet,
    retained_set_null,
)
from angee.base.models.query import (
    AngeeQuerySet,
    AngeeUnscopedQuerySet,
    CandidateRelationships,
    MergeableQuerySet,
    RetainedReferenceQuerySet,
    by_key,
    candidate_relationships,
    model_history,
    query_batch_size,
    record_history,
)
from angee.base.models.stages import Stage, StagedModelMixin, StageTone
from angee.base.models.statements import (
    Aspect,
    PairAspect,
    SourceKind,
    Statement,
    StatementHistory,
    StatementLinkMixin,
    StatementManager,
    StatementPair,
    StatementPairHistory,
    StatementPairManager,
    StatementPairQuerySet,
    StatementQuerySet,
    StatementRecords,
    StatementsMixin,
    StatementSourceMixin,
    StatementSubjectManager,
    StatementSubjectQuerySet,
    Survivorship,
)
from angee.base.models.transitions import (
    Condition,
    StateTransitions,
    SuccessHook,
    TransitionActionSpec,
    TransitionMethod,
    TransitionNotAllowed,
    get_transition_save_field,
    revalidate_transition_metadata,
    save_state,
    transition,
)
from angee.base.models.trash import (
    ARCHIVE_FLAG_FIELD,
    TRASH_FLAG_FIELD,
    TRASH_REASON_MAX_LENGTH,
    ArchiveMixin,
    ArchiveQuerySet,
    TrashMixin,
    TrashQuerySet,
    clean_trash_reason,
)
