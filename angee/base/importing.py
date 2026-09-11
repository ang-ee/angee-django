"""Typed authority and immutable provenance for internal source imports.

``system_context`` remains an audit/REBAC facility.  Import authorization is a
separate, transaction-scoped capability whose source, company, operation, run,
and target scope must all match the static declaration on the target model.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Any, ClassVar, cast

from django.apps import apps
from django.core import checks
from django.core.exceptions import ImproperlyConfigured, PermissionDenied
from django.db import connection, models

from angee.base.models import AngeeManager, AngeeQuerySet


class ImportAuthorityError(PermissionDenied):
    """A write did not carry authority matching its immutable source scope."""


def _required(value: object, label: str) -> str:
    normalized = str(value).strip()
    if not normalized:
        raise ValueError(f"Import {label} is required.")
    return normalized


@dataclass(frozen=True, slots=True)
class ImportSource:
    """Stable identity of one external source connection."""

    type: str
    id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "type", _required(self.type, "source type"))
        object.__setattr__(self, "id", _required(self.id, "source id"))


@dataclass(frozen=True, slots=True)
class ImportCompany:
    """Stable identity of the local company scope authorized for an import."""

    type: str
    id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "type", _required(self.type, "company type"))
        object.__setattr__(self, "id", _required(self.id, "company id"))


@dataclass(frozen=True, slots=True)
class ImportTarget:
    """One model, or one exact row in that model, permitted by a run."""

    model: str
    object_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "model", _required(self.model, "target model").lower())
        if self.object_id is not None:
            object.__setattr__(self, "object_id", _required(self.object_id, "target object id"))

    def permits(self, model: str, object_id: object | None) -> bool:
        """Return whether this target covers ``model`` and optional row identity."""

        return self.model == model.lower() and (
            self.object_id is None or (object_id is not None and self.object_id == str(object_id))
        )


@dataclass(frozen=True, slots=True)
class ImportOperation:
    """Exact authority carried by one trusted internal importer transaction."""

    source: ImportSource
    company: ImportCompany | None
    operation: str
    run: str
    targets: tuple[ImportTarget, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "operation", _required(self.operation, "operation"))
        object.__setattr__(self, "run", _required(self.run, "run"))
        object.__setattr__(self, "targets", tuple(self.targets))
        if not self.targets:
            raise ValueError("Import authority requires at least one target.")


_current_import_operation: ContextVar[ImportOperation | None] = ContextVar(
    "angee_import_operation", default=None
)


def current_import_operation() -> ImportOperation | None:
    """Return the current typed import authority, independent of actor context."""

    return _current_import_operation.get()


def require_import_operation(
    *,
    source: ImportSource,
    company: ImportCompany | None,
    operation: str,
    run: str | None = None,
    target: ImportTarget | None = None,
) -> ImportOperation:
    """Return the current authority or deny when any requested scope differs."""

    current = current_import_operation()
    permitted = (
        current is not None
        and current.source == source
        and current.company == company
        and current.operation == operation
        and (run is None or current.run == run)
        and (
            target is None
            or any(item.permits(target.model, target.object_id) for item in current.targets)
        )
    )
    if not permitted:
        raise ImportAuthorityError("This write requires matching source-scoped import authority.")
    return cast(ImportOperation, current)


def _database_scope(operation: ImportOperation | None) -> None:
    """Set the PostgreSQL transaction-local scope consumed by table guards."""

    if connection.vendor != "postgresql":
        return
    payload = "" if operation is None else json.dumps(
        {
            "source_type": operation.source.type,
            "source_id": operation.source.id,
            "company_type": None if operation.company is None else operation.company.type,
            "company_id": None if operation.company is None else operation.company.id,
            "operation": operation.operation,
            "run": operation.run,
            "targets": [
                {"model": target.model, "object_id": target.object_id} for target in operation.targets
            ],
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    with connection.cursor() as cursor:
        cursor.execute("SELECT set_config('angee.import_scope', %s, true)", (payload,))


@contextmanager
def import_operation(operation: ImportOperation) -> Iterable[ImportOperation]:
    """Open one import capability inside an existing database transaction.

    Callers construct this value only after authenticating the source and run.
    Requiring an existing atomic block makes the capability and the writes it
    authorizes share one commit/rollback boundary. Nested scopes restore the
    outer capability exactly.
    """

    if not connection.in_atomic_block:
        raise ImproperlyConfigured("import_operation() requires an existing transaction.atomic() block.")
    token: Token[ImportOperation | None] = _current_import_operation.set(operation)
    try:
        _database_scope(operation)
        yield operation
    finally:
        _current_import_operation.reset(token)
        _database_scope(current_import_operation())


@dataclass(frozen=True, slots=True)
class ExternalOwnershipDeclaration:
    """Static mutation policy declared by an externally owned target model."""

    source_owned_fields: frozenset[str]
    local_fields: frozenset[str] = frozenset()
    operations: frozenset[str] = frozenset()
    protected_relations: frozenset[str] = frozenset()
    company_field: str | None = None

    def __init__(
        self,
        *,
        source_owned_fields: Iterable[str],
        local_fields: Iterable[str] = (),
        operations: Iterable[str],
        protected_relations: Iterable[str] = (),
        company_field: str | None = None,
    ) -> None:
        object.__setattr__(self, "source_owned_fields", frozenset(source_owned_fields))
        object.__setattr__(self, "local_fields", frozenset(local_fields))
        object.__setattr__(
            self,
            "operations",
            frozenset(_required(value, "declared operation") for value in operations),
        )
        object.__setattr__(self, "protected_relations", frozenset(protected_relations))
        object.__setattr__(self, "company_field", company_field)
        if not self.source_owned_fields:
            raise ValueError("External ownership must declare source-owned fields.")
        if not self.operations:
            raise ValueError("External ownership must declare import operations.")
        overlap = self.source_owned_fields & self.local_fields
        if overlap:
            raise ValueError(f"Fields cannot be both source-owned and local: {sorted(overlap)}")


@dataclass(frozen=True, slots=True)
class ExternalOwnershipContribution:
    """Additive ownership policy supplied by a static ``extends`` donor."""

    source_owned_fields: frozenset[str] = frozenset()
    local_fields: frozenset[str] = frozenset()
    operations: frozenset[str] = frozenset()
    protected_relations: frozenset[str] = frozenset()

    def __init__(
        self,
        *,
        source_owned_fields: Iterable[str] = (),
        local_fields: Iterable[str] = (),
        operations: Iterable[str] = (),
        protected_relations: Iterable[str] = (),
    ) -> None:
        object.__setattr__(self, "source_owned_fields", frozenset(source_owned_fields))
        object.__setattr__(self, "local_fields", frozenset(local_fields))
        object.__setattr__(self, "operations", frozenset(operations))
        object.__setattr__(self, "protected_relations", frozenset(protected_relations))
        overlap = self.source_owned_fields & self.local_fields
        if overlap:
            raise ValueError(f"Contributed fields cannot be both source-owned and local: {sorted(overlap)}")


_PROVENANCE_FIELDS = frozenset({"external_source_type", "external_source_id", "external_source_key"})
_BOOKKEEPING_FIELDS = frozenset({"id", "created_at", "updated_at", "created_by", "updated_by"})


def _declaration(model: type[models.Model]) -> ExternalOwnershipDeclaration:
    policies = tuple(
        base.__dict__["external_ownership"]
        for base in model.__mro__
        if "external_ownership" in base.__dict__
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
    operations = declaration.operations.union(*(contribution.operations for contribution in contributions))
    protected_relations = declaration.protected_relations.union(
        *(contribution.protected_relations for contribution in contributions)
    )
    return ExternalOwnershipDeclaration(
        source_owned_fields=source_fields,
        local_fields=local_fields,
        operations=operations,
        protected_relations=protected_relations,
        company_field=declaration.company_field,
    )


def _source(instance: Any) -> ImportSource | None:
    source_type = str(instance.external_source_type or "")
    source_id = str(instance.external_source_id or "")
    source_key = str(instance.external_source_key or "")
    populated = tuple(bool(value) for value in (source_type, source_id, source_key))
    if any(populated) and not all(populated):
        raise ImportAuthorityError("External provenance must be populated as one complete immutable identity.")
    return ImportSource(source_type, source_id) if all(populated) else None


def _company(instance: Any, declaration: ExternalOwnershipDeclaration) -> ImportCompany | None:
    if declaration.company_field is None:
        return None
    value = instance
    field = None
    for name in declaration.company_field.split("__"):
        field = value._meta.get_field(name)
        value = getattr(value, name, None)
        if value is None:
            return None
    assert field is not None
    company_type = value._meta.label_lower if isinstance(value, models.Model) else field.model._meta.label_lower
    company_id = value.pk if isinstance(value, models.Model) else value
    return ImportCompany(company_type, str(company_id))


def _authorize(instance: Any, *, source: ImportSource, declaration: ExternalOwnershipDeclaration) -> ImportOperation:
    current = current_import_operation()
    if current is None or current.operation not in declaration.operations:
        raise ImportAuthorityError("This write requires a declared import operation.")
    return require_import_operation(
        source=source,
        company=_company(instance, declaration),
        operation=current.operation,
        run=current.run,
        target=ImportTarget(instance._meta.label_lower, instance.pk),
    )


class ExternalOwnershipQuerySet(AngeeQuerySet[Any]):
    """QuerySet that preserves ownership checks across Django bulk APIs."""

    def update(self, **kwargs: Any) -> int:
        declaration = _declaration(self.model)
        changed = frozenset(kwargs)
        if changed & _PROVENANCE_FIELDS:
            raise ImportAuthorityError("External provenance is immutable.")
        governed = changed - declaration.local_fields - _BOOKKEEPING_FIELDS
        company_root = declaration.company_field.split("__", 1)[0] if declaration.company_field else None
        company_names = (
            {company_root, self.model._meta.get_field(company_root).attname}
            if company_root and "__" not in declaration.company_field
            else set()
        )
        if company_names & changed:
            for row in self.only("pk", *_PROVENANCE_FIELDS):
                if _source(row) is not None:
                    raise ImportAuthorityError("External ownership company is immutable.")
        if governed:
            for row in self.only("pk", *_PROVENANCE_FIELDS, *(filter(None, (declaration.company_field,)))):
                if source := _source(row):
                    _authorize(row, source=source, declaration=declaration)
        if declaration.protected_relations & changed:
            for row in self.select_related(*declaration.protected_relations):
                row._check_external_ownership_relations()
        return super().update(**kwargs)

    def delete(self) -> tuple[int, dict[str, int]]:
        declaration = _declaration(self.model)
        for row in self.select_related(*declaration.protected_relations):
            if source := _source(row):
                _authorize(row, source=source, declaration=declaration)
            row._check_external_ownership_relations()
        return super().delete()

    def bulk_create(self, objs: Iterable[Any], *args: Any, **kwargs: Any) -> list[Any]:
        """Guard every row before Django's signal-free batch insertion."""

        rows = list(objs)
        for row in rows:
            row._check_external_ownership_create()
        return super().bulk_create(rows, *args, **kwargs)

    def bulk_update(self, objs: Iterable[Any], fields: Iterable[str], *args: Any, **kwargs: Any) -> int:
        """Guard every row before Django's signal-free batch update."""

        rows = list(objs)
        field_names = tuple(fields)
        for row in rows:
            row._check_external_ownership_update(field_names)
        return super().bulk_update(rows, field_names, *args, **kwargs)


class ExternalOwnershipManager(AngeeManager.from_queryset(ExternalOwnershipQuerySet)):  # type: ignore[misc]
    """Default manager exposing the guarded ownership queryset."""


class ExternalOwnershipMixin(models.Model):
    """Abstract immutable provenance and model-level ownership enforcement."""

    external_source_type = models.CharField(max_length=255, blank=True, editable=False)
    external_source_id = models.CharField(max_length=255, blank=True, editable=False)
    external_source_key = models.CharField(max_length=255, blank=True, editable=False)

    external_ownership: ClassVar[ExternalOwnershipDeclaration]
    objects = ExternalOwnershipManager()

    class Meta:
        abstract = True

    def _check_external_ownership_create(self) -> None:
        declaration = _declaration(type(self))
        if source := _source(self):
            _authorize(self, source=source, declaration=declaration)
        self._check_external_ownership_relations()

    def _check_external_ownership_relations(self) -> None:
        """Require authority for every source-owned foreign-key endpoint."""

        operation = current_import_operation()
        for field_name in _declaration(type(self)).protected_relations:
            endpoint = getattr(self, field_name, None)
            if not isinstance(endpoint, ExternalOwnershipMixin) or _source(endpoint) is None:
                continue
            if operation is None:
                raise ImportAuthorityError("Source-owned endpoint mutation requires import authority.")
            endpoint.require_external_import(operation.operation)

    def require_external_import(self, operation: str) -> ImportOperation:
        """Require exact authority for this owned row and ``operation``.

        Domain policy methods use this owner API instead of reading provenance
        fields or the ambient context themselves.
        """

        declaration = _declaration(type(self))
        if operation not in declaration.operations:
            raise ImportAuthorityError(f"{operation!r} is not declared for {self._meta.label}.")
        source = _source(self)
        if source is None:
            raise ImportAuthorityError("The target has no external source provenance.")
        current = current_import_operation()
        return require_import_operation(
            source=source,
            company=_company(self, declaration),
            operation=operation,
            run=None if current is None else current.run,
            target=ImportTarget(self._meta.label_lower, self.pk),
        )

    def claim_external_ownership(self, source_key: str, operation: str) -> None:
        """Atomically claim one existing unowned row for the current source.

        Natural-key reference adoption uses this path. The row lock and database
        trigger make the empty-to-complete provenance transition indivisible;
        every later provenance change remains forbidden.
        """

        source_key = _required(source_key, "source key")
        current = current_import_operation()
        if current is None or current.operation != operation:
            raise ImportAuthorityError("An ownership claim requires matching import authority.")
        if not connection.in_atomic_block:
            raise ImproperlyConfigured("claim_external_ownership() requires transaction.atomic().")
        declaration = _declaration(type(self))
        if operation not in declaration.operations:
            raise ImportAuthorityError(f"{operation!r} is not declared for {self._meta.label}.")
        row = type(self)._base_manager.select_for_update().get(pk=self.pk)
        if _source(row) is not None:
            raise ImportAuthorityError("External provenance is already claimed and immutable.")
        require_import_operation(
            source=current.source,
            company=_company(row, declaration),
            operation=operation,
            run=current.run,
            target=ImportTarget(self._meta.label_lower, self.pk),
        )
        values = {
            "external_source_type": current.source.type,
            "external_source_id": current.source.id,
            "external_source_key": source_key,
        }
        queryset = type(self)._base_manager.filter(pk=self.pk)
        models.QuerySet.update(queryset, **values)
        for name, value in values.items():
            setattr(self, name, value)

    def is_authorized_import(self, operation: str) -> bool:
        """Return whether the current capability authorizes this exact row."""

        try:
            self.require_external_import(operation)
        except ImportAuthorityError:
            return False
        return True

    def _persisted_ownership(self) -> Any | None:
        if self.pk is None:
            return None
        fields = ("pk", *_PROVENANCE_FIELDS, *(filter(None, (_declaration(type(self)).company_field,))))
        return type(self)._base_manager.filter(pk=self.pk).only(*fields).first()

    def _check_external_ownership_update(self, update_fields: Iterable[str] | None = None) -> None:
        declaration = _declaration(type(self))
        persisted = self._persisted_ownership()
        if persisted is None:
            self._check_external_ownership_create()
            return
        old_source = _source(persisted)
        new_source = _source(self)
        if old_source != new_source or (
            old_source is not None and persisted.external_source_key != self.external_source_key
        ):
            raise ImportAuthorityError("External provenance is immutable.")
        if old_source is not None and _company(persisted, declaration) != _company(self, declaration):
            raise ImportAuthorityError("External ownership company is immutable.")
        if update_fields is None:
            fields = frozenset(
                field.name
                for field in self._meta.concrete_fields
                if field.name not in _PROVENANCE_FIELDS
                and getattr(self, field.attname) != getattr(persisted, field.attname)
            )
        else:
            fields = frozenset(update_fields)
        governed = fields - declaration.local_fields - _BOOKKEEPING_FIELDS
        if old_source is not None and governed:
            _authorize(self, source=old_source, declaration=declaration)
        if declaration.protected_relations & fields:
            self._check_external_ownership_relations()

    def save(self, *args: Any, **kwargs: Any) -> None:
        self._check_external_ownership_update(kwargs.get("update_fields"))
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        declaration = _declaration(type(self))
        if source := _source(self):
            _authorize(self, source=source, declaration=declaration)
        self._check_external_ownership_relations()
        return super().delete(*args, **kwargs)


def check_external_ownership_declarations(
    app_configs: list[object] | None = None, **kwargs: object
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
            declaration = _declaration(model)
            names = {field.name for field in model._meta.concrete_fields} | {
                field.name for field in model._meta.many_to_many
            }
            unknown = (
                declaration.source_owned_fields | declaration.local_fields | declaration.protected_relations
            ) - names
            if declaration.company_field is not None:
                owner = model
                for part in declaration.company_field.split("__"):
                    try:
                        field = owner._meta.get_field(part)
                    except LookupError:
                        unknown.add(declaration.company_field)
                        break
                    owner = field.related_model if field.is_relation else owner
            if unknown:
                raise ImproperlyConfigured(f"unknown field(s): {sorted(unknown)}")
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
            errors.append(checks.Error(str(error), obj=model, id="angee.base.E005"))
    return errors


def check_external_ownership_relation(
    sender: type[models.Model],
    instance: models.Model,
    action: str,
    reverse: bool,
    model: type[models.Model],
    pk_set: set[object] | None,
    using: str,
    **kwargs: object,
) -> None:
    """Protect declared many-to-many relations in both manager directions."""

    del kwargs
    if action not in {"pre_add", "pre_remove", "pre_clear"}:
        return
    operation = current_import_operation()
    if not reverse:
        if not isinstance(instance, ExternalOwnershipMixin):
            return
        declaration = _declaration(type(instance))
        relation = next(
            (
                field
                for field in instance._meta.many_to_many
                if field.remote_field.through is sender and field.name in declaration.source_owned_fields
            ),
            None,
        )
        if relation is not None and _source(instance) is not None:
            if operation is None:
                raise ImportAuthorityError("Source-owned relation mutation requires import authority.")
            instance.require_external_import(operation.operation)
        return

    if not issubclass(model, ExternalOwnershipMixin):
        return
    if pk_set is None:
        related = model._base_manager.using(using).all()
    else:
        related = model._base_manager.using(using).filter(pk__in=pk_set)
    for row in related:
        declaration = _declaration(model)
        if not any(
            field.remote_field.through is sender and field.name in declaration.source_owned_fields
            for field in model._meta.many_to_many
        ):
            continue
        if _source(row) is not None:
            if operation is None:
                raise ImportAuthorityError("Source-owned relation mutation requires import authority.")
            row.require_external_import(operation.operation)
