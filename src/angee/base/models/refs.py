"""Neutral Django generic record edges and their merge/deletion policy.

Declare each GenericRelation on the model its identity owner returns from
``generic_pointer_model`` so forward writes and reverse collection use the same
native contenttype. Optional protection contributes admission at its own owner.
"""

from __future__ import annotations

from collections.abc import Sequence
from functools import lru_cache
from typing import Any, ClassVar, Protocol, cast

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


@lru_cache(maxsize=1)
def _possible_record_edges(
    registered_models: tuple[type[models.Model], ...],
) -> dict[type[models.Model], tuple[type[RecordRefMixin], ...]]:
    """Invert authored edge admission once for the installed model set."""
    index: dict[type[models.Model], list[type[RecordRefMixin]]] = {}
    for edge in sorted(registered_models, key=lambda model: model._meta.label):
        if not issubclass(edge, RecordRefMixin):
            continue
        for target in edge.possible_target_models(registered_models).values():
            index.setdefault(target, []).append(edge)
    return {target: tuple(edges) for target, edges in index.items()}


def possible_record_edges(
    candidate: type[models.Model] | None = None,
) -> dict[type[models.Model], tuple[type[RecordRefMixin], ...]]:
    """Return cached possible edge targets, rebuilding for late prepared models."""
    registered = tuple(apps.get_models())
    if candidate is not None and candidate not in registered:
        registered = (*registered, candidate)
    return _possible_record_edges(registered)


def delete_record_refs_batch(
    sender: type[models.Model],
    *,
    instances: tuple[models.Model, ...],
    using: str,
    **kwargs: Any,
) -> None:
    """Apply each possible edge policy to one admitted collector model batch."""
    del kwargs
    if not instances:
        return
    cleanup_edges = record_ref_cleanup_edges_for_target(sender)
    target = generic_pointer_model(sender)
    declared = possible_record_edges().get(target, ()) if target is not None else ()
    if not cleanup_edges and not declared:
        return
    content_type, _ = generic_pointer_target(instances[0])
    ids = [instance.pk for instance in instances]
    for edge in cleanup_edges:
        pointer = edge.record_ref_field()
        attached = set(
            edge._base_manager.using(using)
            .filter(
                **{
                    pointer.ct_field: content_type,
                    f"{pointer.fk_field}__in": ids,
                }
            )
            .values_list(pointer.fk_field, flat=True)
        )
        teardown = edge.teardown_for_target
        assert teardown is not None
        for instance in instances:
            if instance.pk in attached:
                teardown(instance)
    for edge in declared:
        if edge in cleanup_edges:
            continue
        pointer = edge.record_ref_field()
        object_id_field = edge._meta.get_field(pointer.fk_field)
        if not isinstance(object_id_field, models.Field):
            raise ImproperlyConfigured(f"{edge._meta.label}'s generic object ID must be a concrete field.")
        prepared_ids = []
        for object_id in ids:
            try:
                prepared_ids.append(object_id_field.get_prep_value(object_id))
            except TypeError, ValueError, ValidationError:
                continue
        if not prepared_ids:
            continue
        rows = edge._base_manager.using(using).filter(
            **{
                pointer.ct_field: content_type,
                f"{pointer.fk_field}__in": prepared_ids,
            }
        )
        if edge.on_target_delete is models.PROTECT:
            if rows[:1]:
                raise ProtectedError(f"{edge._meta.label} rows still refer to {sender._meta.label}.", set(rows))
            continue
        edge.remove_target_refs(rows)


def delete_record_refs(instance: models.Model, using: str, **kwargs: Any) -> None:
    """Keep plain Django targets and historical imports on the shared delete policy."""

    del kwargs
    delete_record_refs_batch(type(instance), instances=(instance,), using=using)
