"""Generic record references backed by Django contenttypes.

A polymorphic edge and a REBAC grant see a multi-table-inheritance row from
different sides. An edge stores :func:`rebac.generic_target`, which owns the
canonical write identity: a row is named by its topmost REBAC-typed MTI
ancestor, and a row without a REBAC type cannot be named at all. The two edges
that also name rows outside REBAC — a chatter thread on an ungated host, an
import record link to a plain sink — store :func:`generic_pointer_target`, which falls
back to Django's own generic-pointer identity for such a row.
:func:`ancestor_object_refs` owns the read/grant fan-out,
:meth:`RecordRefMixin.accepted_target_models` reads the target types an edge accepts
from its schema or explicit infrastructure-wide declaration, and
:func:`record_edges_by_target` inverts them into the edges each record type can carry.

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

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Any, ClassVar, cast

from django.apps import apps
from django.contrib.contenttypes.fields import GenericForeignKey, GenericRelation
from django.contrib.contenttypes.models import ContentType
from django.core import checks
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import DEFAULT_DB_ALIAS, models
from django.db.models.deletion import ProtectedError
from django.utils.text import capfirst
from rebac import ObjectRef, system_context, to_object_ref
from rebac.field_backing import canonical_model
from rebac.resources import model_for_resource_type, model_resource_type
from rebac.schema import FieldBinding

from angee.identity import public_data_id_field, public_id_for
from angee.permissions import effective_rebac_definition


class RetainedGenericRelation(GenericRelation):
    """Query retained edges without collecting them on the target's deletion."""

    def bulk_related_objects(self, objs: Sequence[models.Model], using: str = DEFAULT_DB_ALIAS) -> list[models.Model]:
        return []


@dataclass(frozen=True, slots=True)
class RecordRef:
    """Frozen public identity for a model row reached through a generic pointer."""

    model_label: str
    object_id: Any | None
    public_id: str
    resource_type: str


def record_ref_for(instance: models.Model) -> RecordRef:
    """Return the stable public reference for ``instance``."""

    model = type(instance)
    return _record_ref_from_model(model, instance.pk)


def ancestor_object_refs(obj: models.Model) -> tuple[ObjectRef, ...]:
    """Return every REBAC identity ``obj`` IS-A, nearest identity first.

    The **read/grant fan-out** dual of :func:`rebac.generic_target`: ``obj``'s own
    identity first (raises :class:`TypeError` if its model declares no
    ``rebac_resource_type``), then each REBAC-registered concrete MTI ancestor it shares
    a primary key with (``parties.Person`` IS-A ``parties.Party``). Every identity shares
    ``obj``'s REBAC id, so a grant or read on any ancestor type reaches the same row —
    the reason a foreign key typed to a parent still scopes the child in. Returned
    eagerly as a tuple, so the fail-fast fires at the call rather than on first iteration.
    """

    own = to_object_ref(obj)
    refs = [own]
    seen = {own.resource_type}
    for ancestor in _pk_ancestor_chain(type(obj)._meta.concrete_model or type(obj)):
        resource_type = model_resource_type(ancestor)
        if resource_type is not None and resource_type not in seen:
            seen.add(resource_type)
            refs.append(ObjectRef(resource_type, own.resource_id))
    return tuple(refs)


def generic_pointer_model(model: type[models.Model]) -> type[models.Model]:
    """Return the model a generic pointer stores for rows of ``model``.

    A gated row is named by its canonical REBAC model
    (:func:`rebac.field_backing.canonical_model`), so a multi-table child shares
    its typed parent's edges and the schema's target relations reach it. An
    ungated row, which no schema relation can name, keeps its own concrete
    model: Django's generic-pointer default. Only an edge that admits ungated
    rows (a chatter thread, an import record link) uses this; an edge whose
    schema authorizes its target stores :func:`rebac.generic_target` directly.
    """

    return canonical_model(model) or model._meta.concrete_model or model


def generic_pointer_target(record: models.Model) -> tuple[ContentType, Any]:
    """Return the content type and id a generic pointer stores for ``record``.

    :func:`rebac.generic_target`'s identity for a gated row, Django's own for an
    ungated one; see :func:`generic_pointer_model`.
    """

    content_type = ContentType.objects.db_manager(hints={"instance": record}).get_for_model(
        generic_pointer_model(type(record))
    )
    return content_type, record.pk


def _pk_ancestor_chain(model: type[models.Model]) -> Iterator[type[models.Model]]:
    """Yield ``model`` then each concrete MTI ancestor it shares its primary key with.

    Follows only the single primary-key ``parent_link`` at each level. A secondary MTI
    parent keeps its own primary key, so ``model``'s pk does not address that row and
    fanning an edge or grant onto its content type would corrupt; more than one concrete
    parent path is that ambiguous multiple-MTI shape and fails fast.
    """

    current: type[models.Model] | None = model
    while current is not None:
        yield current
        parents = current._meta.parents
        if len(parents) > 1:
            raise ValueError(
                f"{current._meta.label} has more than one concrete parent; a canonical "
                "record target is defined only along a single primary-key MTI chain."
            )
        current = next(iter(parents), None)


def canonical_link_path(model: type[models.Model]) -> tuple[str, ...]:
    """Return the primary-key parent links from ``model`` up to its canonical REBAC model.

    A relation declared on the canonical model, such as the ``GenericRelation`` a
    polymorphic edge keys on through :func:`rebac.generic_target`, is reached from a
    multi-table child through these links: Django's generic relation would key the
    child's rows on the child's own content type, which no edge stores. Empty for a
    canonical model, a proxy of one, or an ungated model.
    """

    target = canonical_model(model)
    if target is None:
        return ()
    chain = list(_pk_ancestor_chain(model._meta.concrete_model or model))
    if target not in chain:
        raise ValueError(f"{model._meta.label} does not share its primary key with {target._meta.label}.")
    links = []
    for step, parent in zip(chain, chain[1:], strict=False):
        if step is target:
            break
        links.append(step._meta.parents[parent].name)
    return tuple(links)


# Deferred on purpose: the merge contract lives in the angee.models package, whose import runs
# angee.evidence and the statement models, which read this module's pointer helpers above. With
# the import here, a process that imports angee.refs first still finds those helpers defined.
from angee.models.merge import (  # noqa: E402
    EdgeReference,
    MergePolicy,
    Reference,
    _RecordRefMergeContract,
)


class RecordRefMixin(_RecordRefMergeContract):
    """Project a row reference from the model's single declared generic foreign key.

    Its collision declarations (``merge_identity``, ``merge_collapse``) are the
    pointing model's own, on :class:`~angee.models.MergeContractMixin`.
    """

    merge_policy: ClassVar[MergePolicy] = MergePolicy.BLOCK
    """What merging the referenced record does to this edge's rows; an edge that declares none blocks."""
    on_target_delete: ClassVar[Any] = models.PROTECT
    """What deleting the referenced record does to this edge's rows."""
    accepts_any_record_target: ClassVar[bool] = False
    """Whether this infrastructure edge may point at every canonical record type."""

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
        if content_type_id in (None, "") or object_id in (None, ""):
            return _empty_record_ref(object_id)
        model = ContentType.objects.get_for_id(content_type_id).model_class()
        if model is None:
            return _empty_record_ref(object_id)
        return _record_ref_from_model(model, object_id)

    @classmethod
    def record_ref_field(cls) -> GenericForeignKey:
        """Return the generic pointer that owns this model's record reference."""

        references = [field for field in cls._meta.private_fields if isinstance(field, GenericForeignKey)]
        if len(references) != 1:
            raise ImproperlyConfigured(f"{cls._meta.label} must declare exactly one GenericForeignKey.")
        return references[0]

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
                if pk is None or public_id_for(model, pk) != value:
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
        if issubclass(edge, RecordRefMixin) and callable(getattr(edge, "teardown_for_target", None))
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


def reference_blocks_target_delete(reference: Reference, target: type[models.Model]) -> bool:
    """Return whether a live reference must prevent deleting its target.

    Ordinary foreign keys and undeclared generic pointers remain blockers. A
    record edge participates when its schema or open-target declaration accepts
    this target, or the target exposes its retained reverse relation for custom teardown.
    The core receiver applies that same policy to ordinary deletion and creation
    undo alike.
    """

    if not isinstance(reference, EdgeReference) or not issubclass(reference.model, RecordRefMixin):
        return True
    canonical = canonical_model(target)
    declared = record_edges_by_target().get(canonical, ()) if canonical is not None else ()
    controlled = {*declared, *record_ref_cleanup_edges_for_target(target)}
    return reference.model not in controlled or reference.model.on_target_delete is models.PROTECT


def delete_record_refs(instance: models.Model, using: str, **kwargs: Any) -> None:
    """Apply each edge owner's declared policy before its target is deleted."""

    del kwargs
    cleanup_edges = record_ref_cleanup_edges_for_target(type(instance))
    for edge in cleanup_edges:
        edge.teardown_for_target(instance)
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
            protected = list(rows)
            if protected:
                raise ProtectedError(f"{edge._meta.label} rows still refer to {instance}.", protected)
            continue
        with system_context(reason="record_ref.target_delete"):
            if policy is models.CASCADE:
                rows.delete()
            elif policy is models.SET_NULL:
                rows.update(**{pointer.ct_field: None, pointer.fk_field: None})


def _record_ref_from_model(model: type[models.Model], object_id: Any) -> RecordRef:
    """Return a record ref from an already resolved model and primary key."""

    return RecordRef(
        model_label=model._meta.label,
        object_id=object_id,
        public_id=public_id_for(model, object_id),
        resource_type=model_resource_type(model) or "",
    )


def _empty_record_ref(object_id: Any | None = None) -> RecordRef:
    """Return the empty reference used for unset or stale contenttypes."""

    return RecordRef(model_label="", object_id=object_id, public_id="", resource_type="")
