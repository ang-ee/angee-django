"""Field ownership for externally maintained projections.

Models declare immutable provenance, source-owned fields and local overlays.
Importers pass plain ``(type, id)`` identities to ``apply_external``; accounting
operation DTOs remain with their accounting addon. REBAC still owns permissions.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from copy import copy
from dataclasses import dataclass
from typing import Any, ClassVar

from django.apps import AppConfig, apps
from django.core import checks
from django.core.exceptions import (
    FieldDoesNotExist,
    ImproperlyConfigured,
    PermissionDenied,
)
from django.db import models, transaction

from angee.base.db import get_write_alias, refresh_deferred, related_on
from angee.base.mixins import AppendOnlyQuerySet, AuditMixin
from angee.base.models import AngeeManager, AngeeQuerySet
from angee.base.permissions import require_authorization_database
from angee.base.transitions import StateTransitions, TransitionNotAllowed


class ExternalOwnershipError(PermissionDenied):
    """A write violates immutable external ownership or retained relationships."""


def _required(value: object, label: str) -> str:
    normalized = str(value).strip()
    if not normalized:
        raise ValueError(f"Import {label} is required.")
    return normalized


def _identity(value: Sequence[str | int], label: str) -> tuple[str, str]:
    """Normalize a native or JSON identity pair before comparing source facts."""

    if isinstance(value, (str, bytes)) or len(value) != 2:
        raise ValueError(f"Import {label} must be a (type, id) pair.")
    return (_required(value[0], f"{label} type"), _required(value[1], f"{label} id"))


@dataclass(frozen=True, slots=True)
class ExternalOwnershipDeclaration:
    """Static mutation policy declared by an externally owned target model."""

    source_owned_fields: frozenset[str]
    local_fields: frozenset[str] = frozenset()
    protected_relations: frozenset[str] = frozenset()
    company_field: str | None = None

    def __init__(
        self,
        *,
        source_owned_fields: Iterable[str],
        local_fields: Iterable[str] = (),
        protected_relations: Iterable[str] = (),
        company_field: str | None = None,
    ) -> None:
        object.__setattr__(self, "source_owned_fields", frozenset(source_owned_fields))
        object.__setattr__(self, "local_fields", frozenset(local_fields))
        object.__setattr__(self, "protected_relations", frozenset(protected_relations))
        object.__setattr__(self, "company_field", company_field)
        if not self.source_owned_fields:
            raise ValueError("External ownership must declare source-owned fields.")
        overlap = self.source_owned_fields & self.local_fields
        if overlap:
            raise ValueError(f"Fields cannot be both source-owned and local: {sorted(overlap)}")

    @classmethod
    def for_model(cls, model: type[models.Model]) -> ExternalOwnershipDeclaration:
        """Combine a model's base policy with its statically composed contributions."""

        policies = tuple(
            base.__dict__["external_ownership"] for base in model.__mro__ if "external_ownership" in base.__dict__
        )
        declarations = tuple(policy for policy in policies if isinstance(policy, ExternalOwnershipDeclaration))
        if not declarations:
            raise ImproperlyConfigured(
                f"{model._meta.label} must declare ExternalOwnershipDeclaration as external_ownership."
            )
        declaration = declarations[0]
        contributions = tuple(policy for policy in policies if isinstance(policy, ExternalOwnershipContribution))
        source_fields = declaration.source_owned_fields.union(
            *(contribution.source_owned_fields for contribution in contributions)
        )
        local_fields = declaration.local_fields.union(*(contribution.local_fields for contribution in contributions))
        protected_relations = declaration.protected_relations.union(
            *(contribution.protected_relations for contribution in contributions)
        )
        return cls(
            source_owned_fields=source_fields,
            local_fields=local_fields,
            protected_relations=protected_relations,
            company_field=declaration.company_field,
        )


@dataclass(frozen=True, slots=True)
class ExternalOwnershipContribution:
    """Additive ownership policy supplied by a static ``extends`` donor."""

    source_owned_fields: frozenset[str] = frozenset()
    local_fields: frozenset[str] = frozenset()
    protected_relations: frozenset[str] = frozenset()

    def __init__(
        self,
        *,
        source_owned_fields: Iterable[str] = (),
        local_fields: Iterable[str] = (),
        protected_relations: Iterable[str] = (),
    ) -> None:
        object.__setattr__(self, "source_owned_fields", frozenset(source_owned_fields))
        object.__setattr__(self, "local_fields", frozenset(local_fields))
        object.__setattr__(self, "protected_relations", frozenset(protected_relations))
        overlap = self.source_owned_fields & self.local_fields
        if overlap:
            raise ValueError(f"Contributed fields cannot be both source-owned and local: {sorted(overlap)}")


_PROVENANCE_FIELDS = frozenset({"external_source_type", "external_source_id", "external_source_key"})
_BOOKKEEPING_FIELDS = frozenset({"id", "created_at", "updated_at", "created_by", "updated_by"})


def _update_field(model: type[models.Model], name: str) -> models.Field[Any, Any]:
    """Resolve a QuerySet update key to its concrete model field."""

    try:
        return model._meta.get_field(name)
    except FieldDoesNotExist:
        field = next(
            (candidate for candidate in model._meta.concrete_fields if candidate.attname == name),
            None,
        )
        if field is None:
            raise
        return field


def _check_transition_fields(instance: Any, persisted: Any, *, using: str) -> None:
    """Validate the complete transition diff, including native save-hook changes."""

    refresh_deferred(instance, using=using)
    allowed = ExternalOwnershipDeclaration.for_model(type(instance)).source_owned_fields
    # These allowances belong to the actual lifecycle owners, not field names
    # that a consumer could coincidentally use for ordinary mutable data.
    lifecycle = {field.name for field in instance._meta.concrete_fields if getattr(field, "auto_now", False)}
    if isinstance(instance, AuditMixin):
        lifecycle.add("updated_by")
    refused = {
        field.name
        for field in instance._meta.concrete_fields
        if field.name not in allowed | lifecycle
        and getattr(instance, field.attname) != getattr(persisted, field.attname)
    }
    if refused:
        raise ExternalOwnershipError(f"External transitions changed undeclared source fields: {sorted(refused)}")


def run_external_transition(
    instance: Any,
    source: Sequence[str | int],
    transition: str,
    *,
    using: str | None = None,
    **kwargs: Any,
) -> Any:
    """Run a declared native transition under an explicit matching source claim.

    The native body, graph, conditions, success hook and cooperative model save
    remain authoritative. Its success hook must accept/forward ``persist`` to
    ``save_state``. Every changed concrete field must be source-owned, except
    native ``auto_now`` fields and ``AuditMixin.updated_by``. Any other change
    rolls back the complete transition. No import authority is retained on the
    instance or in ambient context; nested writes need their own explicit claim.
    """

    if not isinstance(instance, ExternalOwnershipMixin):
        raise ExternalOwnershipError("External transitions require an ownership projection.")
    alias = get_write_alias(type(instance), using=using, instance=instance)
    require_authorization_database(alias, operation="External projection transition")
    source = _identity(source, "source")
    specs = {spec.name: spec for spec in StateTransitions.action_specs(type(instance))}
    if transition not in specs:
        raise ExternalOwnershipError("External imports require a declared native transition.")
    if "persist" in kwargs:
        raise ExternalOwnershipError("External transition persistence belongs to the ownership owner.")
    instance._state.db = alias
    with transaction.atomic(using=alias):
        persisted = instance._persisted_ownership(using=alias, for_update=True)
        if persisted is None:
            raise ExternalOwnershipError("External transitions require a saved projection.")
        refresh_deferred(instance, using=alias)
        persisted.require_external_identity(
            source=source,
            source_key=instance.external_source_key,
            company=instance.import_company(using=alias),
            using=alias,
        )
        state_field = specs[transition].field
        if getattr(instance, state_field) != getattr(persisted, state_field):
            raise TransitionNotAllowed("The native transition state changed before external admission.")

        def persist(row: Any, *, using: str, update_fields: Iterable[str]) -> None:
            if row is not instance or using != alias:
                raise ExternalOwnershipError("External transition persistence belongs to its admitted projection.")
            row.save(using=using, update_fields=update_fields, external_source=source)

        result = getattr(instance, transition)(using=alias, persist=persist, **kwargs)
        _check_transition_fields(instance, persisted, using=alias)
        applied = instance._persisted_ownership(using=alias)
        if applied is None:
            raise ExternalOwnershipError("External transitions cannot delete their projection.")
        applied._check_external_ownership_update(persisted=persisted, source=source, using=alias)
        _check_transition_fields(applied, persisted, using=alias)
        return result


class ExternalOwnershipQuerySet(AngeeQuerySet[Any]):
    """QuerySet that preserves ownership checks across Django bulk APIs."""

    def update(self, *, using: str | None = None, **kwargs: Any) -> int:
        """Preserve immutable identity and validate proposed relationship values."""

        if self.query.is_sliced or self.query.combinator or self._fields is not None:
            raise TypeError("Ownership updates require an unsliced model queryset.")
        using = get_write_alias(self.model, using=using, bound=self)
        require_authorization_database(using, operation="External ownership update")
        queryset = self.using(using)
        queryset._for_write = True
        declaration = ExternalOwnershipDeclaration.for_model(queryset.model)
        fields = {name: _update_field(queryset.model, name) for name in kwargs}
        changed = frozenset(field.name for field in fields.values())
        if changed & _PROVENANCE_FIELDS:
            raise ExternalOwnershipError("External provenance is immutable.")
        scope_fields = set(declaration.protected_relations | declaration.source_owned_fields)
        if declaration.company_field:
            scope_fields.add(declaration.company_field.split("__", 1)[0])
        if not changed & scope_fields:
            return super(ExternalOwnershipQuerySet, queryset).update(**kwargs)
        # Freeze rows under ordered locks before evaluating expressions, including
        # the CASE expressions emitted by native bulk_update.
        with transaction.atomic(using=using):
            rows = tuple(queryset.defer(None).order_by("pk").lock_if_supported())
            frozen = queryset.filter(pk__in=[row.pk for row in rows])
            expressions = {
                f"_ownership_value_{index}": value
                for index, (name, value) in enumerate(kwargs.items())
                if fields[name].name in scope_fields and hasattr(value, "resolve_expression")
            }
            evaluated = (
                {row["pk"]: row for row in frozen.annotate(**expressions).values("pk", *expressions)}
                if expressions
                else {}
            )
            for persisted in rows:
                proposed = copy(persisted)
                proposed._state = copy(persisted._state)
                proposed._state.fields_cache = dict(persisted._state.fields_cache)
                for index, (name, value) in enumerate(kwargs.items()):
                    field = fields[name]
                    if field.name not in scope_fields:
                        continue
                    if hasattr(value, "resolve_expression"):
                        value = evaluated[persisted.pk][f"_ownership_value_{index}"]
                    if isinstance(value, models.Model):
                        value = value.pk
                    proposed._state.fields_cache.pop(field.name, None)
                    setattr(proposed, field.attname, value)
                proposed._check_external_ownership_update(changed, persisted=persisted, using=using)
            return super(ExternalOwnershipQuerySet, frozen).update(**kwargs)

    def delete(self, *, using: str | None = None) -> tuple[int, dict[str, int]]:
        """Validate retained rows before the collector handles cascades."""

        if self.query.is_sliced or self.query.combinator or self._fields is not None:
            raise TypeError("Ownership deletes require an unsliced model queryset.")
        using = get_write_alias(self.model, using=using, bound=self)
        require_authorization_database(using, operation="External ownership deletion")
        queryset = self.using(using)
        queryset._for_write = True
        with transaction.atomic(using=using):
            rows = tuple(queryset.defer(None).order_by("pk").lock_if_supported())
            for row in rows:
                row._check_external_ownership_delete(using=using)
            frozen = queryset.filter(pk__in=[row.pk for row in rows])
            return super(ExternalOwnershipQuerySet, frozen).delete()

    def bulk_create(
        self,
        objs: Iterable[Any],
        batch_size: int | None = None,
        ignore_conflicts: bool = False,
        update_conflicts: bool = False,
        update_fields: Iterable[str] | None = None,
        unique_fields: Iterable[str] | None = None,
        *,
        using: str | None = None,
    ) -> list[Any]:
        """Validate inserted rows; conflict updates cannot preserve ownership."""

        if update_conflicts:
            raise ExternalOwnershipError("Ownership rows cannot be rewritten by conflict insertion.")
        rows = list(objs)
        using = get_write_alias(self.model, using=using, bound=self)
        require_authorization_database(using, operation="External ownership creation")
        queryset = self.using(using)
        queryset._for_write = True
        with transaction.atomic(using=using):
            for row in rows:
                row._state.db = using
                row._check_external_ownership_create(using=using)
            return super(ExternalOwnershipQuerySet, queryset).bulk_create(
                rows,
                batch_size=batch_size,
                ignore_conflicts=ignore_conflicts,
                update_conflicts=update_conflicts,
                update_fields=update_fields,
                unique_fields=unique_fields,
            )

    def bulk_update(
        self, objs: Iterable[Any], fields: Iterable[str], *args: Any, using: str | None = None, **kwargs: Any
    ) -> int:
        """Keep arbitrary bulk changes to protected fields closed."""

        field_names = tuple(fields)
        using = get_write_alias(self.model, using=using, bound=self)
        require_authorization_database(using, operation="External ownership bulk update")
        queryset = self.using(using)
        queryset._for_write = True
        declaration = ExternalOwnershipDeclaration.for_model(queryset.model)
        changed = {_update_field(queryset.model, name).name for name in field_names}
        if changed - declaration.local_fields - _BOOKKEEPING_FIELDS:
            raise ExternalOwnershipError(
                "Source-owned fields require apply_external(); "
                "local relationship changes require validated_bulk_update()."
            )
        return queryset.validated_bulk_update(objs, field_names, *args, using=using, **kwargs)

    def validated_bulk_update(
        self,
        objs: Iterable[Any],
        fields: Iterable[str],
        *args: Any,
        using: str | None = None,
        **kwargs: Any,
    ) -> int:
        """Lock rows in PK order, validate their facts, then use Django's batch write.

        Native bulk_update re-enters update(), which independently checks the
        actual proposed company/relation values. The composed queryset and actor
        permissions remain in the MRO; no temporary admission state is installed.
        """

        rows = list(objs)
        field_names = tuple(fields)
        if not rows:
            return 0
        identities = [row.pk for row in rows]
        if any(identity is None for identity in identities) or len(identities) != len(set(identities)):
            raise ValueError("validated_bulk_update() requires distinct saved ownership rows.")
        using = get_write_alias(self.model, using=using, bound=self)
        require_authorization_database(using, operation="External ownership validated bulk update")
        queryset = self.using(using)
        queryset._for_write = True
        with transaction.atomic(using=using):
            persisted = {
                row.pk: row for row in queryset.filter(pk__in=identities).defer(None).order_by("pk").lock_if_supported()
            }
            if set(persisted) != set(identities):
                raise ValueError("Ownership rows changed before the write.")
            for row in rows:
                row._state.db = using
                row._check_external_ownership_update(field_names, persisted=persisted[row.pk], using=using)
            return super(ExternalOwnershipQuerySet, queryset).bulk_update(rows, field_names, *args, **kwargs)


class ExternalOwnershipManager(AngeeManager.from_queryset(ExternalOwnershipQuerySet)):  # type: ignore[misc]
    """Default manager exposing the guarded ownership queryset."""

    def apply_external(
        self,
        pk: Any,
        values: Mapping[str, Any],
        *,
        source: Sequence[str | int],
        source_key: str,
        company: Sequence[str | int] | None,
        using: str | None = None,
    ) -> Any:
        """Apply source fields to an existing projection with matching provenance.

        This explicit import command owns the row lock and immutable identity
        check. It changes only declared concrete source fields; local overlays
        remain local. Composed queryset permission checks remain in the MRO.
        Create projections with complete provenance through native ``create``.
        """

        using = get_write_alias(self.model, using=using, bound=self)
        require_authorization_database(using, operation="External projection import")
        queryset = self.db_manager(using).get_queryset()
        if isinstance(queryset, AppendOnlyQuerySet):
            raise ExternalOwnershipError("Append-only projections cannot be updated by an import.")
        declaration = ExternalOwnershipDeclaration.for_model(self.model)
        fields = {name: _update_field(self.model, name) for name in values}
        if any(
            field.name not in declaration.source_owned_fields or not field.concrete or field.many_to_many
            for field in fields.values()
        ):
            raise ExternalOwnershipError("External imports may only write declared source fields.")
        if any(hasattr(value, "resolve_expression") for value in values.values()):
            raise ExternalOwnershipError("External imports require concrete source values.")
        with transaction.atomic(using=using):
            persisted = queryset.filter(pk=pk).lock_if_supported().get()
            source = persisted.require_external_identity(
                source=source,
                source_key=source_key,
                company=company,
                using=using,
            )
            proposed = copy(persisted)
            proposed._state = copy(persisted._state)
            proposed._state.fields_cache = dict(persisted._state.fields_cache)
            changes = {}
            for name, value in values.items():
                field = fields[name]
                value = value.pk if isinstance(value, models.Model) else value
                proposed._state.fields_cache.pop(field.name, None)
                setattr(proposed, field.attname, value)
                changes[field.attname] = value
            proposed._check_external_ownership_update(
                values,
                persisted=persisted,
                using=using,
                source=source,
            )
            super(ExternalOwnershipQuerySet, queryset.filter(pk=pk)).update(**changes)
            return proposed


class ExternalOwnershipMixin(models.Model):
    """Abstract immutable provenance and model-level ownership enforcement."""

    external_source_type = models.CharField(max_length=255, blank=True, editable=False)
    external_source_id = models.CharField(max_length=255, blank=True, editable=False)
    external_source_key = models.CharField(max_length=255, blank=True, editable=False)

    external_ownership: ClassVar[ExternalOwnershipDeclaration]
    objects = ExternalOwnershipManager()

    class Meta:
        abstract = True

    def import_source(self, *, using: str | None = None) -> tuple[str, str] | None:
        """Resolve this row's complete provenance or reject a partial identity."""

        using = get_write_alias(type(self), using=using, instance=self)
        refresh_deferred(self, using=using, fields=_PROVENANCE_FIELDS)
        source_type = str(self.external_source_type or "")
        source_id = str(self.external_source_id or "")
        source_key = str(self.external_source_key or "")
        populated = tuple(bool(value) for value in (source_type, source_id, source_key))
        if any(populated) and not all(populated):
            raise ExternalOwnershipError("External provenance must be populated as one complete immutable identity.")
        return (source_type, source_id) if all(populated) else None

    def import_company(self, *, using: str | None = None) -> tuple[str, str] | None:
        """Resolve the company scope through this row's declared field path."""

        declaration = ExternalOwnershipDeclaration.for_model(type(self))
        if declaration.company_field is None:
            return None
        alias = get_write_alias(type(self), using=using, instance=self)
        self._state.db = alias
        refresh_deferred(self, using=alias)
        value = self
        field = None
        for name in declaration.company_field.split("__"):
            field = value._meta.get_field(name)
            identity = getattr(value, field.attname, None)
            if identity is None:
                return None
            value = related_on(value, name, using=alias) if field.is_relation else identity
        assert field is not None
        company_type = value._meta.label_lower if isinstance(value, models.Model) else field.model._meta.label_lower
        company_id = value.pk if isinstance(value, models.Model) else value
        return (company_type, str(company_id))

    def require_external_identity(
        self,
        *,
        source: Sequence[str | int],
        source_key: str,
        company: Sequence[str | int] | None,
        using: str | None = None,
    ) -> tuple[str, str]:
        """Validate native or JSON identity pairs and return the canonical source."""

        using = get_write_alias(type(self), using=using, instance=self)
        source = _identity(source, "source")
        company = _identity(company, "company") if company is not None else None
        self._state.db = using
        refresh_deferred(self, using=using)
        actual_source = self.import_source(using=using)
        if (
            actual_source is None
            or actual_source != source
            or self.external_source_key != source_key
            or self.import_company(using=using) != company
        ):
            raise ExternalOwnershipError("The projection belongs to a different source identity or company.")
        return actual_source

    def _check_external_ownership_create(self, *, using: str) -> None:
        self.import_source(using=using)
        self._check_external_ownership_relations(using=using)

    def _check_external_ownership_relations(self, *, using: str) -> None:
        """An owned endpoint may only be linked from the same source and company."""

        source = self.import_source(using=using)
        for name in sorted(ExternalOwnershipDeclaration.for_model(type(self)).protected_relations):
            field = self._meta.get_field(name)
            identity = getattr(self, field.attname)
            if identity is None:
                continue
            endpoint = related_on(self, name, using=using)
            if not isinstance(endpoint, ExternalOwnershipMixin) or endpoint.import_source(using=using) is None:
                continue
            if source != endpoint.import_source(using=using) or self.import_company(
                using=using
            ) != endpoint.import_company(using=using):
                raise ExternalOwnershipError("Source-owned endpoints must share source and company scope.")

    def _check_external_ownership_delete(self, *, using: str) -> None:
        """Retain source rows and local rows attached to protected source endpoints."""

        if self.import_source(using=using) is not None:
            raise ExternalOwnershipError("Externally owned rows cannot be deleted.")
        self._check_external_ownership_relations(using=using)

    def _check_external_ownership_claim(self, *, using: str) -> None:
        """Allow model owners to close the one-time provenance claim path."""

        del using

    def claim_external_ownership(
        self, source_key: str, *, source: Sequence[str | int], using: str | None = None
    ) -> None:
        """Claim an unowned row once under its row lock, using explicit source facts."""

        using = get_write_alias(type(self), using=using, instance=self)
        source = _identity(source, "source")
        self._state.db = using
        source_key = _required(source_key, "source key")
        require_authorization_database(using, operation="External ownership claim")
        with transaction.atomic(using=using):
            row = type(self).objects.db_manager(using).filter(pk=self.pk).lock_if_supported().get()
            if row.import_source(using=using) is not None:
                raise ExternalOwnershipError("External provenance is already claimed and immutable.")
            row._check_external_ownership_claim(using=using)
            values = {
                "external_source_type": source[0],
                "external_source_id": source[1],
                "external_source_key": source_key,
            }
            for name, value in values.items():
                setattr(row, name, value)
            row._check_external_ownership_relations(using=using)
            queryset = type(self).objects.db_manager(using).filter(pk=row.pk)
            # Only this owner admits the empty-to-complete transition. Later
            # domain/REBAC queryset behavior remains in the cooperative MRO.
            super(ExternalOwnershipQuerySet, queryset).update(**values)
            for name, value in values.items():
                setattr(self, name, value)
            self._state.db = using

    def _persisted_ownership(
        self,
        *,
        using: str,
        for_update: bool = False,
    ) -> Any | None:
        if self.pk is None:
            return None
        queryset = (
            type(self)
            .system_queryset(
                using=using,
                lock=("self",) if for_update else None,
            )
            .filter(pk=self.pk)
        )
        return queryset.first()

    def _check_external_ownership_update(
        self,
        update_fields: Iterable[str] | None = None,
        *,
        persisted: Any | None = None,
        using: str,
        source: tuple[str, str] | None = None,
    ) -> None:
        declaration = ExternalOwnershipDeclaration.for_model(type(self))
        alias = using
        if persisted is None:
            persisted = self._persisted_ownership(using=alias)
        if persisted is None:
            self._check_external_ownership_create(using=alias)
            return
        # Deferred attributes must come from the locked operation snapshot,
        # never an implicit read-routed refresh through Django descriptors.
        for name in self.get_deferred_fields():
            setattr(self, name, getattr(persisted, name))
        old_source = persisted.import_source(using=using)
        new_source = self.import_source(using=using)
        if old_source != new_source or (
            old_source is not None and persisted.external_source_key != self.external_source_key
        ):
            raise ExternalOwnershipError("External provenance is immutable.")
        if old_source is not None and persisted.import_company(using=alias) != self.import_company(using=alias):
            raise ExternalOwnershipError("External ownership company is immutable.")
        if update_fields is None:
            fields = frozenset(
                field.name
                for field in self._meta.concrete_fields
                if field.name not in _PROVENANCE_FIELDS
                and getattr(self, field.attname) != getattr(persisted, field.attname)
            )
        else:
            fields = frozenset(_update_field(type(self), name).name for name in update_fields)
        if old_source is not None and source != old_source:
            changed_owned = {
                name
                for name in declaration.source_owned_fields & fields
                if getattr(self, self._meta.get_field(name).attname)
                != getattr(persisted, self._meta.get_field(name).attname)
            }
            if changed_owned:
                raise ExternalOwnershipError(f"Source-owned fields require apply_external(): {sorted(changed_owned)}")
        if declaration.protected_relations & fields:
            persisted._check_external_ownership_relations(using=alias)
            self._check_external_ownership_relations(using=alias)

    def save(
        self,
        *args: Any,
        using: str | None = None,
        external_source: Sequence[str | int] | None = None,
        **kwargs: Any,
    ) -> None:
        """Retain provenance; explicit transition saves verify their complete diff.

        ``external_source`` is persistence plumbing for the explicit transition
        owner. It is a matching source identity, never ambient import authority.
        """

        alias = get_write_alias(type(self), using=using, instance=self)
        require_authorization_database(alias, operation="External ownership save")
        self._state.db = alias
        with transaction.atomic(using=alias):
            persisted = self._persisted_ownership(using=alias, for_update=True)
            source = None if external_source is None else _identity(external_source, "source")
            if source is not None:
                if persisted is None or persisted.import_source(using=alias) != source:
                    raise ExternalOwnershipError("The projection belongs to a different source identity.")
                _check_transition_fields(self, persisted, using=alias)
            self._check_external_ownership_update(
                kwargs.get("update_fields"),
                persisted=persisted,
                using=alias,
                source=source,
            )
            kwargs["using"] = alias
            super().save(*args, **kwargs)
            if source is not None:
                applied = self._persisted_ownership(using=alias)
                if applied is None:
                    raise ExternalOwnershipError("External transitions cannot delete their projection.")
                applied._check_external_ownership_update(persisted=persisted, source=source, using=alias)
                _check_transition_fields(applied, persisted, using=alias)

    def delete(self, *args: Any, using: str | None = None, **kwargs: Any) -> tuple[int, dict[str, int]]:
        """Lock and retain external rows before Django collects deletion edges."""

        alias = get_write_alias(type(self), using=using, instance=self)
        require_authorization_database(alias, operation="External ownership deletion")
        self._state.db = alias
        with transaction.atomic(using=alias):
            row = self._persisted_ownership(using=alias, for_update=True)
            if row is None:
                return (0, {})
            row._check_external_ownership_delete(using=alias)
            kwargs["using"] = alias
            return super().delete(*args, **kwargs)


def check_external_ownership_declarations(
    app_configs: list[AppConfig] | None = None, **kwargs: object
) -> list[checks.CheckMessage]:
    """Validate static target declarations after Django app population."""

    del kwargs
    selected = None if app_configs is None else {config.label for config in app_configs}
    errors: list[checks.CheckMessage] = []
    for model in apps.get_models():
        if selected is not None and model._meta.app_label not in selected:
            continue
        if not issubclass(model, ExternalOwnershipMixin):
            continue
        try:
            declaration = ExternalOwnershipDeclaration.for_model(model)
            names = {field.name for field in model._meta.concrete_fields} | {
                field.name for field in model._meta.many_to_many
            }
            unknown = set(
                (declaration.source_owned_fields | declaration.local_fields | declaration.protected_relations) - names
            )
            if declaration.company_field is not None:
                owner = model
                parts = declaration.company_field.split("__")
                for index, part in enumerate(parts):
                    try:
                        field = owner._meta.get_field(part)
                    except FieldDoesNotExist:
                        unknown.add(declaration.company_field)
                        break
                    if field.is_relation:
                        if not field.concrete or field.many_to_many or field.related_model is None:
                            raise ImproperlyConfigured("company scope must follow concrete foreign keys")
                        owner = field.related_model
                    elif index < len(parts) - 1:
                        raise ImproperlyConfigured("company scope cannot traverse a scalar field")
            if unknown:
                raise ImproperlyConfigured(f"unknown field(s): {sorted(unknown)}")
            source_many_to_many = {
                field.name for field in model._meta.many_to_many if field.name in declaration.source_owned_fields
            }
            if source_many_to_many:
                raise ImproperlyConfigured(
                    "Source-owned many-to-many fields require explicit ownership on "
                    f"their through model instead: {sorted(source_many_to_many)}"
                )
            invalid_relations = {
                name
                for name in declaration.protected_relations
                if not model._meta.get_field(name).is_relation or model._meta.get_field(name).many_to_many
            }
            if invalid_relations:
                raise ImproperlyConfigured(f"protected relation(s) must be foreign keys: {sorted(invalid_relations)}")
            declared = declaration.source_owned_fields | declaration.local_fields | _PROVENANCE_FIELDS
            undeclared = {
                field.name
                for field in (*model._meta.concrete_fields, *model._meta.many_to_many)
                if not field.auto_created and field.name not in declared and field.name not in _BOOKKEEPING_FIELDS
            }
            if undeclared:
                raise ImproperlyConfigured(f"field(s) lack source/local ownership: {sorted(undeclared)}")
        except (ImproperlyConfigured, ValueError) as error:
            errors.append(checks.Error(str(error), obj=model, id="integrate.E005"))
    return errors


def check_external_ownership_delete(
    sender: type[models.Model],
    instance: models.Model,
    *,
    using: str | None = None,
    **kwargs: object,
) -> None:
    """Apply the row's retention rule to every collected cascade child."""

    del sender, kwargs
    if isinstance(instance, ExternalOwnershipMixin):
        using = get_write_alias(type(instance), using=using, instance=instance)
        require_authorization_database(using, operation="External ownership cascade deletion")
        row = type(instance).system_queryset(using=using, lock=("self",)).get(pk=instance.pk)
        row._check_external_ownership_delete(using=using)
