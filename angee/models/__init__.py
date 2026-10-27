"""Django-shaped model toolkit for Angee applications."""

# ruff: noqa: F401 -- this module is the public re-export surface.

from angee.errors import CreationKeyConflict
from angee.lineage import (
    concrete_child,
    concrete_child_accessor,
    concrete_child_models,
    concrete_lineage,
    most_derived_concrete,
)
from angee.models.append_only import (
    AppendOnlyBaseManager,
    AppendOnlyBaseQuerySet,
    AppendOnlyModel,
    AppendOnlyQuerySet,
)
from angee.models.audit import AuditMixin, retained_set_null
from angee.models.base import (
    AngeeDataModel,
    AngeeModel,
    DirectRecordAccess,
    record_display_label,
    role_anchor,
)
from angee.models.derived import DerivedFrom
from angee.models.hierarchy import HierarchyMixin, HierarchyQuerySet
from angee.models.history import ChangesetQuerySet, HistoryMixin, ModelHistory, ModelHistoryManager
from angee.models.identity import (
    SqidMixin,
    TimestampMixin,
    auto_now_stamps,
    update_fields_with_auto_now,
)
from angee.models.inputs import InputExtensionsMixin
from angee.models.locks import (
    ImmutableFieldsMixin,
    OptimisticLockMixin,
    RowLockMixin,
    StaleRevisionError,
    require_revision,
    validate_revision,
)
from angee.models.manager import AngeeManager, AngeeUnscopedManager
from angee.models.merge import (
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
from angee.models.ownership import (
    ItemOwnershipMixin,
    OwnerMixin,
    OwnerQuerySet,
)
from angee.models.query import (
    AngeeQuerySet,
    AngeeUnscopedQuerySet,
    CandidateRelationships,
    MergeableQuerySet,
    RetainedReferenceQuerySet,
    SystemQuerysetMixin,
    by_key,
    candidate_relationships,
    model_history,
    query_batch_size,
    record_history,
    row_lock_paths,
)
from angee.models.refs import RecordRefMixin
from angee.models.stages import Stage, StagedModelMixin, StageTone
from angee.models.statements import (
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
    prime_readable_statement_records,
)
from angee.models.transitions import (
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
from angee.models.trash import (
    ARCHIVE_FLAG_FIELD,
    TRASH_FLAG_FIELD,
    TRASH_REASON_MAX_LENGTH,
    ArchiveMixin,
    ArchiveQuerySet,
    TrashMixin,
    TrashQuerySet,
    clean_trash_reason,
)
