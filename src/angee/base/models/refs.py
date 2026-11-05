"""Neutral Django generic record edges and their merge/deletion policy.

Declare each GenericRelation on the model its identity owner returns from
``generic_pointer_model`` so forward writes and reverse collection use the same
native contenttype. Optional protection contributes admission at its own owner.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from contextvars import ContextVar
from operator import attrgetter
from typing import Any, ClassVar, Protocol, cast
from weakref import ReferenceType, ref

from django.apps import apps
from django.contrib.contenttypes.fields import GenericForeignKey, GenericRelation
from django.contrib.contenttypes.models import ContentType
from django.core import checks
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import models
from django.db.models.deletion import ProtectedError
from django_compose.models import Composable

from angee.base.identity import public_data_id_field
from angee.base.models.identity import public_id_for
from angee.base.models.merge import MergeableMixin
from angee.base.models.merge_references import MergePolicy, RecordRefMergeContract
from angee.base.querysets import MergeableQuerySet
from angee.base.refs import RecordRef, generic_pointer_model, generic_pointer_target


class RecordPublicIDOperand(models.Case):
    """A generic reference ID operand that can decode its one known target."""

    def __init__(self, *cases: models.When, keys: dict[str, Any]) -> None:
        super().__init__(*cases, default=models.Value(""), output_field=models.CharField())
        self.keys = keys

    def key_for_model(self, model: type[models.Model]) -> Any | None:
        """Return the key decoded for this target model, if the ID names one."""

        return self.keys.get(model._meta.label_lower)

    @staticmethod
    def decode_key(model: type[models.Model], value: str) -> Any | None:
        """Use the target's native public identity, rejecting invalid values."""

        field = public_data_id_field(model)
        try:
            pk = field.public_id_to_value(value) if field else model._meta.pk.to_python(value)
            if pk is None or (field is None and public_id_for(model, pk) != value):
                return None
        except TypeError, ValueError, ValidationError:
            return None
        return pk


class _TargetTeardown(Protocol):
    def __call__(self, target: models.Model) -> None: ...


class _TargetsTeardown(Protocol):
    def __call__(self, content_type: ContentType, ids: tuple[Any, ...], *, using: str) -> None: ...


class RecordRefMixin(RecordRefMergeContract, Composable, concrete=False):
    """Project a row reference from the model's single declared generic foreign key.

    Its collision declarations (``merge_identity``, ``merge_collapse``) are the
    pointing model's own, on :class:`~angee.base.models.merge_references.MergeContractMixin`.
    """

    merge_policy: ClassVar[MergePolicy] = MergePolicy.BLOCK
    """What merging the referenced record does to this edge's rows; an edge that declares none blocks."""
    on_target_delete: ClassVar[Any] = models.PROTECT
    """What deleting the referenced record does to this edge's rows."""
    teardown_for_target: ClassVar[_TargetTeardown | None] = None
    """Optional class hook replacing ordinary deletion policy with the edge owner's cleanup."""
    teardown_for_targets: ClassVar[_TargetsTeardown | None] = None
    """Batch form of the same cleanup, when the edge owns one."""

    class Meta:
        """Django model options for record-ref-only abstract inheritance."""

        abstract = True

    @classmethod
    def check(cls, **kwargs: Any) -> list[checks.CheckMessage]:
        """Reject ambiguous generic pointers and invalid deletion policy."""

        errors = super().check(**kwargs)
        references = [field for field in cls._meta.private_fields if isinstance(field, GenericForeignKey)]
        try:
            reference = cls.record_ref_field()
        except ImproperlyConfigured:
            errors.append(
                checks.Error(
                    f"{cls._meta.label} must declare exactly one GenericForeignKey for RecordRefMixin; "
                    f"found {len(references)}.",
                    obj=cls,
                    id="angee.E029",
                )
            )
            reference = None
        if cls.on_target_delete not in (models.CASCADE, models.PROTECT, models.SET_NULL):
            errors.append(
                checks.Error(
                    f"{cls._meta.label}.on_target_delete must be CASCADE, PROTECT, or SET_NULL.",
                    obj=cls,
                    id="angee.E039",
                )
            )
        elif cls.on_target_delete is models.SET_NULL and reference is not None:
            pointer = reference
            if not cls._meta.get_field(pointer.ct_field).null or not cls._meta.get_field(pointer.fk_field).null:
                errors.append(
                    checks.Error(
                        f"{cls._meta.label}.on_target_delete = SET_NULL requires nullable pointer fields.",
                        obj=cls,
                        id="angee.E039",
                    )
                )
        return errors

    @property
    def record_ref(self) -> RecordRef:
        """Return this row's referenced record identity without loading the target."""

        reference = self.record_ref_field()
        content_type_id = getattr(self, reference.ct_field_attname)
        object_id = getattr(self, reference.fk_field)
        model = (
            ContentType.objects.db_manager(self._state.db).get_for_id(content_type_id).model_class()
            if content_type_id not in (None, "") and object_id not in (None, "") else None
        )
        if model is None:
            return RecordRef(model_label="", object_id=object_id, public_id="")
        return RecordRef.from_model(model, object_id)

    @classmethod
    def blocks_target_delete(cls, target: type[models.Model]) -> bool:
        """Use the same owned edge policy for ordinary deletion and creation undo."""

        canonical = generic_pointer_model(target)
        declared = record_edges_by_target().get(canonical, ()) if canonical is not None else ()
        controlled = {*declared, *record_ref_cleanup_edges_for_target(target)}
        return cls not in controlled or cls.on_target_delete is models.PROTECT

    @classmethod
    def record_ref_field(cls) -> GenericForeignKey:
        """Return the generic pointer that owns this model's record reference."""

        references = [field for field in cls._meta.private_fields if isinstance(field, GenericForeignKey)]
        if len(references) != 1:
            raise ImproperlyConfigured(f"{cls._meta.label} must declare exactly one GenericForeignKey.")
        return references[0]

    @classmethod
    def record_filter(cls, record: models.Model) -> models.Q:
        """Select this edge's canonical target, including retained merge references.

        KEEP edges preserve historical identities. Their record reads include
        the survivor's native redirect group without rewriting the evidence.
        Other policies already move or refuse references at merge time.
        """
        return cls.record_filter_batch((record,))

    @classmethod
    def record_filter_batch(cls, records: Sequence[models.Model]) -> models.Q:
        """Select one target model's identities, including retained redirects, in one predicate."""

        if not records:
            return models.Q(pk__in=[])
        content_type, _ = generic_pointer_target(records[0])
        reference = cls.record_ref_field()
        model = generic_pointer_model(type(records[0]))
        ids = [record.pk for record in records]
        scope: dict[str, object] = {reference.ct_field: content_type}
        if cls.merge_policy == MergePolicy.KEEP and issubclass(model, MergeableMixin):
            group = model._base_manager.using(records[0]._state.db)
            if not isinstance(group, MergeableQuerySet):
                raise ImproperlyConfigured(f"{model._meta.label}'s base queryset must support merge redirects.")
            group = group.redirect_groups(cast(Sequence[MergeableMixin], records)).order_by()
            scope[f"{reference.fk_field}__in"] = group.values("pk")
        else:
            scope[reference.fk_field if len(ids) == 1 else f"{reference.fk_field}__in"] = (
                ids[0] if len(ids) == 1 else ids
            )
        return models.Q(**scope)

    @classmethod
    def accepted_target_models(cls) -> dict[str, type[models.Model]]:
        """Normal Django generic pointers accept every installed concrete record."""
        return {
            model._meta.label_lower: model
            for model in sorted(cls._meta.apps.get_models(), key=lambda model: model._meta.label_lower)
            if not model._meta.auto_created
        }

    @classmethod
    def possible_target_models(
        cls,
        registered_models: Sequence[type[models.Model]] | None = None,
    ) -> dict[str, type[models.Model]]:
        """Targets this edge may name before a runtime policy snapshot exists."""
        models_to_check = registered_models if registered_models is not None else cls._meta.apps.get_models()
        return {model._meta.label_lower: model for model in models_to_check if not model._meta.auto_created}

    @classmethod
    def indexed_target_models(
        cls, source: object | None, *, definition_required: bool = False,
    ) -> dict[str, type[models.Model]]:
        """Targets admitted by the policy source shared by one index build."""
        del source, definition_required
        return cls.accepted_target_models()

    @staticmethod
    def target_index_source(*, declared: bool = False) -> object | None:
        """Return the policy snapshot used by :meth:`indexed_target_models`."""
        del declared
        return None

    @classmethod
    def validate_target(cls, target: models.Model) -> None:
        """Require a saved target; domain and protection donors own further admission."""
        if target.pk is None or target._state.adding:
            raise ValidationError({"target": "A record reference requires a saved target."})

    @classmethod
    def remove_target_refs(cls, rows: models.QuerySet[Any]) -> None:
        """Apply this edge's deletion policy through native Django persistence."""
        pointer = cls.record_ref_field()
        if cls.on_target_delete is models.CASCADE:
            rows.delete()
        elif cls.on_target_delete is models.SET_NULL:
            rows.update(**{pointer.ct_field: None, pointer.fk_field: None})

    @classmethod
    def record_edge_roles(cls, target: type[models.Model]) -> tuple[str, ...]:
        """Return the roles of this edge that records of ``target`` carry, beyond the edge itself.

        An edge whose rows play more than one role (a thread attached as a record's
        chatter or as a related conversation) names each role a target type admits.
        The target's resource lists them as ``<edge label>:<role>`` beside the edge
        label, so a surface gates on the role it serves. The default declares none.
        """

        del target
        return ()

    @classmethod
    def record_public_id_operand(cls, value: str) -> RecordPublicIDOperand:
        """Decode a public ID to a SQL operand bound to the pointer's model.

        Generic references carry both a content type and an object ID. Decode
        through each target's existing identity field, retaining its content type
        in a native CASE expression so equal numeric keys on different models
        cannot alias. No target rows or content types are fetched to filter rows.
        """

        reference = cls.record_ref_field()
        candidates = []
        keys = {}
        for model in sorted(cls._meta.apps.get_models(), key=lambda item: item._meta.label_lower):
            pk = RecordPublicIDOperand.decode_key(model, value)
            if pk is None:
                continue
            keys[model._meta.label_lower] = pk
            candidates.append(
                models.When(
                    **{
                        f"{reference.ct_field}__app_label": model._meta.app_label,
                        f"{reference.ct_field}__model": model._meta.model_name,
                    },
                    then=models.Value(str(pk)),
                )
            )
        return RecordPublicIDOperand(*candidates, keys=keys)

    @property
    def record_model_label(self) -> str:
        """Return the referenced record's ``app_label.ModelName`` label."""

        return self.record_ref.model_label

    @property
    def record_public_id(self) -> str:
        """Return the referenced record's stable public id."""

        return self.record_ref.public_id


_target_edge_index: dict[tuple[type[RecordRefMixin], bool], tuple[
    object,
    tuple[tuple[type[RecordRefMixin], object | None], ...],
    dict[type[models.Model], tuple[type[RecordRefMixin], ...]],
]] = {}


def record_edges_by_target(
    *, declared: bool = False, edge_type: type[RecordRefMixin] = RecordRefMixin,
    definition_required: bool = False,
) -> dict[type[models.Model], tuple[type[RecordRefMixin], ...]]:
    """Invert edge admission once per model set and schema snapshot identity."""
    registered_models = apps.get_models()
    edges = tuple(
        edge for edge in sorted(registered_models, key=lambda model: model._meta.label)
        if issubclass(edge, edge_type)
    )
    policy_sources: dict[object, object | None] = {}
    sources = []
    for edge in edges:
        provider = edge.target_index_source
        if provider not in policy_sources:
            policy_sources[provider] = provider(declared=declared)
        sources.append((edge, policy_sources[provider]))
    signature = tuple(sources)
    # Authored files can be rebound or rewritten during composition; only native
    # effective snapshots have a revision identity suitable for reuse.
    cache_key = (edge_type, definition_required)
    cached = _target_edge_index.get(cache_key) if not declared else None
    if cached is not None and registered_models is cached[0] and len(signature) == len(cached[1]) and all(
        edge is cached_edge and source is cached_source
        for (edge, source), (cached_edge, cached_source) in zip(signature, cached[1], strict=True)
    ):
        return cached[2]
    index: dict[type[models.Model], list[type[RecordRefMixin]]] = {}
    for edge, source in signature:
        for target in edge.indexed_target_models(source, definition_required=definition_required).values():
            index.setdefault(target, []).append(edge)
    result = {target: tuple(target_edges) for target, target_edges in index.items()}
    if not declared:
        _target_edge_index[cache_key] = (registered_models, signature, result)
    return result


def record_ref_cleanup_edges_for_target(
    target: type[models.Model],
) -> tuple[type[RecordRefMixin], ...]:
    """Return cleanup edges named by ``target``'s retained reverse relations."""

    edges: set[type[RecordRefMixin]] = set()
    for field in target._meta.private_fields:
        if not isinstance(field, GenericRelation):
            continue
        related = field.remote_field.model
        if isinstance(related, str):
            try:
                related = target._meta.apps.get_model(related)
            except LookupError, ValueError:
                continue
        if issubclass(related, RecordRefMixin) and related.teardown_for_target is not None:
            edges.add(related)
    return tuple(sorted(edges, key=lambda edge: edge._meta.label))


_possible_edge_index: tuple[
    tuple[type[models.Model], ...],
    tuple[tuple[type[RecordRefMixin], tuple[type[models.Model], ...]], ...],
    dict[type[models.Model], tuple[type[RecordRefMixin], ...]],
] | None = None


def possible_record_edges(
    candidate: type[models.Model] | None = None,
) -> dict[type[models.Model], tuple[type[RecordRefMixin], ...]]:
    """Index current authored targets, including rebinding and late prepared models."""
    global _possible_edge_index

    registered = tuple(apps.get_models())
    if candidate is not None and candidate not in registered:
        registered = (*registered, candidate)
    admissions = tuple(
        (edge, tuple(sorted(edge.possible_target_models(registered).values(), key=lambda model: model._meta.label)))
        for edge in sorted(registered, key=lambda model: model._meta.label)
        if issubclass(edge, RecordRefMixin)
    )
    cached = _possible_edge_index
    if cached is not None and registered == cached[0] and admissions == cached[1]:
        return cached[2]
    index: dict[type[models.Model], list[type[RecordRefMixin]]] = {}
    for edge, targets in admissions:
        for target in targets:
            index.setdefault(target, []).append(edge)
    result = {target: tuple(edges) for target, edges in index.items()}
    _possible_edge_index = (registered, admissions, result)
    return result


_edge_presence_for_delete: ContextVar[
    tuple[tuple[ReferenceType[Any], dict[type[RecordRefMixin], bool]], ...]
] = ContextVar(
    "record_edge_presence_for_delete", default=(),
)


def _has_edge_to_admitted_targets(
    edge: type[RecordRefMixin], admitted: dict[type[models.Model], set[Any]], using: str,
) -> bool:
    """Probe the delete scope's admitted target identities through the edge's base manager."""
    pointer = edge.record_ref_field()
    object_id_field = edge._meta.get_field(pointer.fk_field)
    if not isinstance(object_id_field, models.Field):
        raise ImproperlyConfigured(f"{edge._meta.label}'s generic object ID must be a concrete field.")
    grouped: dict[type[models.Model], set[Any]] = {}
    for model, ids in admitted.items():
        grouped.setdefault(generic_pointer_model(model), set()).update(ids)
    lookup = models.Q()
    parameters = 0
    for model in sorted(grouped, key=lambda target: target._meta.label):
        prepared = []
        for pk in sorted(grouped[model]):
            try:
                prepared.append(object_id_field.get_prep_value(pk))
            except TypeError, ValueError, ValidationError:
                continue
        for start in range(0, len(prepared), 256):
            part = prepared[start:start + 256]
            if parameters + len(part) + 2 > 512 and lookup.children:
                if edge._base_manager.using(using).filter(lookup).exists():
                    return True
                lookup = models.Q()
                parameters = 0
            lookup |= models.Q(**{
                f"{pointer.ct_field}__app_label": model._meta.app_label,
                f"{pointer.ct_field}__model": model._meta.model_name,
                f"{pointer.fk_field}__in": part,
            })
            parameters += len(part) + 2
    return bool(lookup.children and edge._base_manager.using(using).filter(lookup).exists())


def delete_record_refs_batch(
    sender: type[models.Model], *, instances: Iterable[models.Model], using: str, origin: Any = None,
) -> None:
    """Apply each target edge once per collected model batch.

    Edge rows referring to collected targets are reachable through those
    already-admitted parents. Base-manager probes are structural reads for
    applying ``on_target_delete``, not delivery of edge rows to a caller.
    An edge with no rows pointing at the admitted target set needs one probe
    for the whole delete scope.
    """
    targets = tuple(sorted(instances, key=attrgetter("pk")))
    if not targets:
        return
    cleanup_edges = record_ref_cleanup_edges_for_target(sender)
    canonical = generic_pointer_model(sender)
    edge_index = possible_record_edges()
    declared = edge_index.get(canonical, ()) if canonical is not None else ()
    if not declared and not cleanup_edges:
        return
    scope = None
    if origin is not None and apps.is_installed("rebac"):
        from rebac.mixins import delete_scope

        scope = delete_scope(origin, using)
    cached_presence = tuple(entry for entry in _edge_presence_for_delete.get() if entry[0]() is not None)
    presence = next((rows for owner, rows in cached_presence if owner() is scope), None)
    if scope is not None and presence is None:
        presence = {}
        _edge_presence_for_delete.set((*cached_presence, (ref(scope), presence)))
    grouped: dict[ContentType, list[tuple[Any, models.Model]]] = {}
    for instance in targets:
        content_type, object_id = generic_pointer_target(instance)
        grouped.setdefault(content_type, []).append((object_id, instance))
    prepared_edges = []
    for edge in declared:
        if edge in cleanup_edges:
            continue
        if scope is not None and sender in scope.covered_pks:
            assert presence is not None
            if edge not in presence:
                admitted = {
                    model: ids for model, ids in scope.covered_pks.items()
                    if edge in edge_index.get(generic_pointer_model(model), ())
                }
                presence[edge] = _has_edge_to_admitted_targets(edge, admitted, using)
            if not presence[edge]:
                continue
        pointer = edge.record_ref_field()
        object_id_field = edge._meta.get_field(pointer.fk_field)
        if not isinstance(object_id_field, models.Field):
            raise ImproperlyConfigured(f"{edge._meta.label}'s generic object ID must be a concrete field.")
        for content_type, entries in grouped.items():
            prepared: list[tuple[Any, models.Model]] = []
            for object_id, instance in entries:
                try:
                    prepared.append((object_id_field.get_prep_value(object_id), instance))
                except TypeError, ValueError, ValidationError:
                    continue
            for start in range(0, len(prepared), 256):
                part = prepared[start:start + 256]
                if part:
                    prepared_edges.append((edge, pointer, content_type, part))
    protected: dict[models.Model, tuple[type[RecordRefMixin], set[RecordRefMixin]]] = {}
    for edge, pointer, content_type, part in prepared_edges:
        if edge.on_target_delete is not models.PROTECT:
            continue
        blocking_rows = list(edge._base_manager.using(using).filter(**{
            pointer.ct_field: content_type,
            f"{pointer.fk_field}__in": [pk for pk, _ in part],
        }).only("pk", pointer.fk_field))
        if not blocking_rows:
            continue
        by_id: dict[Any, set[RecordRefMixin]] = {}
        for row in blocking_rows:
            by_id.setdefault(getattr(row, pointer.fk_field), set()).add(row)
        matched = False
        for pk, instance in part:
            if blockers := by_id.get(pk):
                protected.setdefault(instance, (edge, blockers))
                matched = True
        if not matched:
            protected.setdefault(part[0][1], (edge, set(blocking_rows)))
    for instance in targets:
        if instance in protected:
            edge, blockers = protected[instance]
            raise ProtectedError(f"{edge._meta.label} rows still refer to {instance}.", blockers)
    for edge in cleanup_edges:
        teardown_batch = edge.teardown_for_targets
        if teardown_batch is None:
            teardown = edge.teardown_for_target
            assert teardown is not None
            for instance in targets:
                teardown(instance)
        else:
            for content_type, entries in grouped.items():
                for start in range(0, len(entries), 256):
                    teardown_batch(content_type, tuple(pk for pk, _ in entries[start:start + 256]), using=using)
    for edge, pointer, content_type, part in prepared_edges:
        if edge.on_target_delete is not models.PROTECT:
            edge_rows = edge._base_manager.using(using).filter(**{
                pointer.ct_field: content_type,
                f"{pointer.fk_field}__in": [pk for pk, _ in part],
            })
            if edge_rows.exists():
                edge.remove_target_refs(edge_rows)


def delete_record_refs_collected(
    sender: type[models.Model], *, instances: Iterable[models.Model], using: str, **kwargs: Any,
) -> None:
    """Receive the native collector's admitted model batch."""
    delete_record_refs_batch(sender, instances=instances, using=using, origin=kwargs.get("origin"))


def delete_record_refs(
    sender: type[models.Model], *, instance: models.Model, using: str, origin: Any = None, **kwargs: Any,
) -> None:
    """Apply each edge owner's declared policy before its target is deleted."""

    del kwargs
    if apps.is_installed("rebac"):
        from rebac.mixins import batch_delivered

        if batch_delivered(origin, using, sender):
            return
    delete_record_refs_batch(sender, instances=(instance,), using=using, origin=origin)
