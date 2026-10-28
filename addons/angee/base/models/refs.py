"""Model-owned generic record edges, accepted targets and deletion policy.

**Placement invariant.** Every polymorphic edge and every reverse
``GenericRelation`` onto one (``messaging.ThreadedModelMixin.thread_attachments``,
``projects.ProjectBindingsMixin.project_bindings``) must be declared on, and any
mixin owning it composed onto, the *same* topmost REBAC-typed MTI ancestor the
canonical write keys on. A reverse ``GenericRelation`` filters at its declaring
model's own content type, so composing the mixin on a child while its canonical
ancestor does not splits the write content type from the collect content type
and orphans edge rows on delete.
"""

from __future__ import annotations

from typing import Any, ClassVar, Protocol, cast

from django.apps import apps
from django.contrib.contenttypes.fields import GenericForeignKey, GenericRelation
from django.contrib.contenttypes.models import ContentType
from django.core import checks
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import models
from django.db.models.deletion import ProtectedError
from django.utils.text import capfirst
from rebac import system_context
from rebac.field_backing import canonical_model
from rebac.resources import model_for_resource_type
from rebac.schema import FieldBinding

from angee.base.identity import public_data_id_field, public_id_for
from angee.base.models.merge import MergeableMixin
from angee.base.models.merge_references import MergePolicy, RecordRefMergeContract
from angee.base.permissions import effective_rebac_definition
from angee.base.querysets import MergeableQuerySet
from angee.base.refs import RecordRef, generic_pointer_model, generic_pointer_target


class _TargetTeardown(Protocol):
    def __call__(self, target: models.Model) -> None: ...


class RecordRefMixin(RecordRefMergeContract):
    """Project a row reference from the model's single declared generic foreign key.

    Its collision declarations (``merge_identity``, ``merge_collapse``) are the
    pointing model's own, on :class:`~angee.base.models.MergeContractMixin`.
    """

    merge_policy: ClassVar[MergePolicy] = MergePolicy.BLOCK
    """What merging the referenced record does to this edge's rows; an edge that declares none blocks."""
    on_target_delete: ClassVar[Any] = models.PROTECT
    """What deleting the referenced record does to this edge's rows."""
    accepts_any_record_target: ClassVar[bool] = False
    """Whether this infrastructure edge may point at every canonical record type."""
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
            ContentType.objects.get_for_id(content_type_id).model_class()
            if content_type_id not in (None, "") and object_id not in (None, "") else None
        )
        if model is None:
            return RecordRef(model_label="", object_id=object_id, public_id="", resource_type="")
        return RecordRef.from_model(model, object_id)

    @classmethod
    def blocks_target_delete(cls, target: type[models.Model]) -> bool:
        """Use the same owned edge policy for ordinary deletion and creation undo."""

        canonical = canonical_model(target)
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
        content_type, pk = generic_pointer_target(record)
        reference = cls.record_ref_field()
        model = generic_pointer_model(type(record))
        scope: dict[str, object] = {reference.ct_field: content_type}
        if cls.merge_policy == MergePolicy.KEEP and issubclass(model, MergeableMixin):
            assert isinstance(record, MergeableMixin)
            group = model._base_manager.using(record._state.db)
            if not isinstance(group, MergeableQuerySet):
                raise ImproperlyConfigured(f"{model._meta.label}'s base queryset must support merge redirects.")
            group = group.redirect_group(record).order_by()
            scope[f"{reference.fk_field}__in"] = group.values("pk")
        else:
            scope[reference.fk_field] = pk
        return models.Q(**scope)

    @classmethod
    def declared_target_models(cls) -> dict[str, type[models.Model]]:
        """Return the target models this edge's effective schema declares, by ``label_lower``.

        One relation per target type, backed by the generic pointer
        (``// rebac:field=target``), names the types the edge may hold under an
        actor. Owners derive their accepted-target sets from this one
        declaration instead of listing the types again.
        """

        definition = effective_rebac_definition(cls)
        if definition is None:
            raise ImproperlyConfigured(f"{cls._meta.label} declares no REBAC definition to read target types from.")
        pointer = cls.record_ref_field().name
        targets: dict[str, type[models.Model]] = {}
        for relation in definition.relations:
            backing = relation.backing
            if not isinstance(backing, FieldBinding) or backing.path != pointer:
                continue
            model = model_for_resource_type(relation.allowed_subjects[0].type)
            if model is not None:
                targets[model._meta.label_lower] = model
        return targets

    @classmethod
    def declared_target_model(cls, model_label: str) -> type[models.Model]:
        """Return the declared target a ``app_label.model`` label names, or a ``ValidationError``.

        A label names its canonical type, as :meth:`declares_target` decides: a
        multi-table child (a person) addresses its typed parent (a party).
        """

        try:
            model = apps.get_model(str(model_label).strip())
        except (LookupError, ValueError) as error:
            raise cls._undeclared_target(cls.declared_target_models()) from error
        if not cls.declares_target(model):
            raise cls._undeclared_target(cls.declared_target_models())
        return cast(type[models.Model], canonical_model(model))

    @classmethod
    def accepted_target_models(cls) -> dict[str, type[models.Model]]:
        """Return the complete target set from the schema or the explicit open edge."""

        if not cls.accepts_any_record_target:
            return cls.declared_target_models()
        targets = {
            target._meta.label_lower: target
            for model in cls._meta.apps.get_models()
            if (target := canonical_model(model)) is not None
        }
        return dict(sorted(targets.items()))

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
    def declares_target(cls, model: type[models.Model]) -> bool:
        """Whether this edge's schema declares ``model``'s canonical type a target.

        A record of an undeclared type cannot carry the edge under an actor: the
        edge's ``create`` has no arm for it and :meth:`validate_target` refuses it.
        """

        target = canonical_model(model)
        return target is not None and target._meta.label_lower in cls.accepted_target_models()

    @classmethod
    def validate_target(cls, target: models.Model) -> None:
        """Reject a target whose canonical model the edge's schema does not declare."""

        if not cls.declares_target(type(target)):
            raise cls._undeclared_target(cls.declared_target_models())

    @classmethod
    def _undeclared_target(cls, declared: dict[str, type[models.Model]]) -> ValidationError:
        names = ", ".join(sorted(str(model._meta.verbose_name) for model in declared.values()))
        return ValidationError({"target": f"{capfirst(str(cls._meta.verbose_name))} may target only: {names}."})

    @classmethod
    def record_public_id_operand(cls, value: str) -> models.Case:
        """Decode a public ID to a SQL operand bound to the pointer's model.

        Generic references carry both a content type and an object ID. Decode
        through each target's existing identity field, retaining its content type
        in a native CASE expression so equal numeric keys on different models
        cannot alias. No target rows or content types are fetched to filter rows.
        """

        reference = cls.record_ref_field()
        candidates = []
        for model in sorted(cls._meta.apps.get_models(), key=lambda item: item._meta.label_lower):
            field = public_data_id_field(model)
            try:
                pk = field.public_id_to_value(value) if field else model._meta.pk.to_python(value)
                if pk is None or (field is None and public_id_for(model, pk) != value):
                    continue
            except TypeError, ValueError, ValidationError:
                continue
            candidates.append(
                models.When(
                    **{
                        f"{reference.ct_field}__app_label": model._meta.app_label,
                        f"{reference.ct_field}__model": model._meta.model_name,
                    },
                    then=models.Value(str(pk)),
                )
            )
        return models.Case(*candidates, default=models.Value(""), output_field=models.CharField())

    @property
    def record_model_label(self) -> str:
        """Return the referenced record's ``app_label.ModelName`` label."""

        return self.record_ref.model_label

    @property
    def record_public_id(self) -> str:
        """Return the referenced record's stable public id."""

        return self.record_ref.public_id


def record_edges_by_target() -> dict[type[models.Model], tuple[type[RecordRefMixin], ...]]:
    """Index installed edges by every canonical target they accept, in label order.

    The inverse of :meth:`RecordRefMixin.accepted_target_models`: the polymorphic
    edges a record can carry, keyed by the canonical model
    :func:`rebac.generic_target` names it by. Infrastructure edges can explicitly
    accept every canonical target without declaring an independent row policy.
    """

    index: dict[type[models.Model], list[type[RecordRefMixin]]] = {}
    for edge in sorted(apps.get_models(), key=lambda model: model._meta.label):
        if not issubclass(edge, RecordRefMixin):
            continue
        if not edge.accepts_any_record_target and effective_rebac_definition(edge) is None:
            continue
        for target in edge.accepted_target_models().values():
            index.setdefault(target, []).append(edge)
    return {target: tuple(edges) for target, edges in index.items()}


def record_ref_cleanup_edges() -> tuple[type[RecordRefMixin], ...]:
    """Return installed edge models declaring target-specific teardown."""

    return tuple(
        edge
        for edge in sorted(apps.get_models(), key=lambda model: model._meta.label)
        if issubclass(edge, RecordRefMixin) and edge.teardown_for_target is not None
    )


def record_ref_cleanup_edges_for_target(
    target: type[models.Model],
) -> tuple[type[RecordRefMixin], ...]:
    """Return cleanup edges named by ``target``'s retained reverse relations."""

    cleanup = record_ref_cleanup_edges()
    return tuple(
        edge for edge in cleanup if record_ref_cleanup_target(edge, target)
    )


def record_ref_cleanup_target(edge: type[RecordRefMixin], target: type[models.Model]) -> bool:
    """Return whether ``target`` declares a retained reverse relation to ``edge``."""

    for field in target._meta.private_fields:
        if not isinstance(field, GenericRelation):
            continue
        related = field.remote_field.model
        if related is edge:
            return True
        if isinstance(related, str) and related.lower() == edge._meta.label_lower:
            return True
    return False




def delete_record_refs(instance: models.Model, using: str, **kwargs: Any) -> None:
    """Apply each edge owner's declared policy before its target is deleted."""

    del kwargs
    cleanup_edges = record_ref_cleanup_edges_for_target(type(instance))
    for edge in cleanup_edges:
        teardown = edge.teardown_for_target
        assert teardown is not None
        teardown(instance)
    target = canonical_model(type(instance))
    declared = record_edges_by_target().get(target, ()) if target is not None else ()
    content_type, object_id = generic_pointer_target(instance)
    for edge in declared:
        if edge in cleanup_edges:
            continue
        pointer = edge.record_ref_field()
        rows = edge._base_manager.using(using).filter(
            **{pointer.ct_field: content_type, pointer.fk_field: object_id}
        )
        policy = edge.on_target_delete
        if policy is models.PROTECT:
            protected: set[models.Model] = set(rows)
            if protected:
                raise ProtectedError(f"{edge._meta.label} rows still refer to {instance}.", protected)
            continue
        with system_context(reason="record_ref.target_delete"):
            if policy is models.CASCADE:
                rows.delete()
            elif policy is models.SET_NULL:
                rows.update(**{pointer.ct_field: None, pointer.fk_field: None})
