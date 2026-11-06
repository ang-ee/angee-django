"""Dashboard creation, copy-on-write snapshots and compare-and-swap factories."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from django.core.exceptions import ValidationError
from django.db import IntegrityError, router, transaction
from rebac import PermissionDenied, current_actor, database_context, system_context, to_subject_ref

from angee.dashboards.exceptions import DashboardConflictError
from angee.dashboards.querysets import DashboardQuerySet, DashboardWidgetQuerySet
from angee.dashboards.validators import canonical_dashboard_snapshot, validate_dashboard_queries
from angee.provenance.context import changeset, changeset_model, require_changeset
from angee.provenance.contrib.angee.managers import AngeeHistoryManager

if TYPE_CHECKING:
    from angee.dashboards.models import Dashboard, DashboardWidget

_DashboardManagerBase = AngeeHistoryManager.from_queryset(DashboardQuerySet)


class DashboardManager[DashboardT: Dashboard](_DashboardManagerBase[DashboardT]):
    """Own target resolution and snapshot writes, including CAS and child diffs."""

    def for_target(self, owner: Any, scope: str, scope_key: str | None) -> DashboardT | None:
        """Return the readable target, preferring the actor's authored snapshot."""

        return self.target_candidates(owner, scope, scope_key).first()

    def may_create(self, owner: Any, actor: Any = None) -> Any:
        """Authorize the actor to create an ordinary (non-baseline) dashboard owned by ``owner``.

        The create gate excludes installed baselines through the row's own flag, so
        the check runs on the candidate row rather than on its relationships alone.
        """

        return self.model(owner=owner, is_baseline=False).require_access("create", actor or current_actor())

    def create_personal(self, owner: Any, *, name: str, description: str = "", idempotency_key: str) -> DashboardT:
        actor = self.may_create(owner)
        if to_subject_ref(owner) != actor:
            raise PermissionDenied("A personal dashboard can only be created for the acting user.")
        name = name.strip() or "Untitled dashboard"
        claim_values = {"name": name, "description": description}
        using = self._db or router.db_for_write(self.model, instance=owner)
        with database_context(using), changeset(fresh=True, using=using), transaction.atomic(using=using):
            dashboard = changeset_model().objects.db_manager(using).claim_created(
                f"dashboards.personal:{actor}",
                idempotency_key,
                values=claim_values,
                model=self.model,
                unavailable=ValidationError(
                    {"idempotency_key": "The claimed dashboard is unavailable."},
                ),
            )
            if dashboard is not None:
                return dashboard.with_actor(actor)
            dashboard = self.model(
                owner=owner,
                scope=self.model.DashboardScope.PERSONAL,
                scope_key=None,
                name=name,
                description=description,
            )
            dashboard.full_clean()
            dashboard.sudo(reason="dashboards.create_personal").save(using=using)
            require_changeset().record_created(dashboard)
            return dashboard.with_actor(actor)

    def duplicate_personal(
        self,
        owner: Any,
        *,
        source: DashboardT,
        name: str,
        idempotency_key: str,
        snapshot: Any,
    ) -> DashboardT:
        """Copy the displayed snapshot of a readable source into one personal dashboard.

        The supplied snapshot can include newer declarations than the persisted
        source. Archived widgets and definition identities do not enter the copy.
        Creation and widget persistence share one transaction and replay key.
        """

        source.require_access("read", current_actor())
        visible = canonical_dashboard_snapshot(snapshot)
        widgets = []
        for index, widget in enumerate(visible["widgets"]):
            if widget["isArchived"]:
                continue
            duplicate = {**widget, "id": f"copy-{index + 1}"}
            duplicate.pop("definitionRef", None)
            widgets.append(duplicate)
        visible["widgets"] = widgets
        using = self._db or router.db_for_write(self.model, instance=owner)
        rows = self.db_manager(using)
        with database_context(using), transaction.atomic(using=using):
            created = rows.create_personal(owner, name=name, idempotency_key=idempotency_key)
            if created.revision > 1:
                return created
            return rows.save_snapshot(
                owner,
                scope=self.model.DashboardScope.PERSONAL,
                scope_key=None,
                persisted_id=created.pk,
                expected_revision=created.revision,
                snapshot=visible,
                name=created.name,
            )

    def save_snapshot(
        self,
        owner: Any,
        *,
        scope: str,
        scope_key: str | None,
        persisted_id: int | None,
        expected_revision: int | None,
        snapshot: Any,
        declaration_revision: str = "",
        name: str = "",
        description: str = "",
    ) -> DashboardT:
        canonical = canonical_dashboard_snapshot(snapshot)
        validate_dashboard_queries(canonical)
        actor = current_actor()
        if actor is None:
            raise PermissionDenied("Dashboard snapshots require an acting user.")
        if scope not in self.model.DashboardScope.values:
            raise ValidationError({"scope": "Unknown dashboard scope."})
        using = self._db or router.db_for_write(self.model, instance=owner)
        with (
            database_context(using),
            transaction.atomic(using=using),
            system_context(reason="dashboards.save_snapshot"),
        ):
            current = None
            if persisted_id is not None:
                current = self.model.system_queryset(lock=("self",)).using(using).filter(pk=persisted_id).first()
                if current is None:
                    raise DashboardConflictError()
                if scope != self.model.DashboardScope.PERSONAL and current.owner_id != owner.pk:
                    raise DashboardConflictError()
                if expected_revision is None:
                    raise DashboardConflictError(current.revision)
                current.require_revision(expected_revision)
                if current.scope != scope or current.scope_key != scope_key:
                    raise DashboardConflictError(current.revision)
            else:
                if to_subject_ref(owner) != actor:
                    raise PermissionDenied("New scoped dashboards can only be created for the acting user.")
                if expected_revision is not None:
                    raise DashboardConflictError()
                if scope == self.model.DashboardScope.PERSONAL:
                    raise ValidationError({"scope": "Create personal dashboards before saving them."})
                current = (
                    self.model.system_queryset(lock=("self",)).using(using)
                    .filter(
                        owner=owner,
                        scope=scope,
                        scope_key=scope_key,
                    )
                    .first()
                )
                if current is not None:
                    raise DashboardConflictError(current.revision)
                current = self.model(
                    owner=owner,
                    scope=scope,
                    scope_key=scope_key,
                    name=name or scope_key or "Dashboard",
                    description=description,
                    columns=canonical["columns"],
                    declaration_revision=declaration_revision,
                )
                current.full_clean()
                try:
                    with transaction.atomic(using=using):
                        current.sudo(reason="dashboards.materialize").save(using=using)
                except IntegrityError as error:
                    raise DashboardConflictError() from error

            if current.scope != self.model.DashboardScope.PERSONAL and to_subject_ref(current.owner) != actor:
                raise PermissionDenied("Scoped dashboards can only be edited by their owner.")
            if not current.with_actor(actor).has_access("write"):
                raise PermissionDenied("You cannot edit this dashboard.")
            widget_model = current._meta.get_field("widgets").related_model
            assert widget_model is not None
            widget_model = cast("type[DashboardWidget]", widget_model)
            existing_widgets = {
                widget.widget_key: widget
                for widget in widget_model.system_queryset(lock=("self",)).using(using).filter(dashboard=current)
            }
            changed = current.columns != canonical["columns"] or current.declaration_revision != declaration_revision
            dashboard_fields: list[str] = []
            if name and current.name != name:
                current.name = name
                dashboard_fields.append("name")
            if current.description != description:
                current.description = description
                dashboard_fields.append("description")
            changed = changed or bool(dashboard_fields)
            seen: set[str] = set()
            for sequence, item in enumerate(canonical["widgets"]):
                seen.add(item["id"])
                widget = existing_widgets.get(item["id"])
                fields = {
                    "definition_ref": item.get("definitionRef"),
                    "spec_version": item["schemaVersion"],
                    "kind": item["kind"],
                    "kind_version": item["kindVersion"],
                    "title": item["title"],
                    "data": item["data"],
                    "options": item["options"],
                    "visibility": item.get("visibility"),
                    "x": item["x"],
                    "y": item["y"],
                    "w": item["w"],
                    "h": item["h"],
                    "sequence": sequence,
                    "is_archived": item["isArchived"],
                }
                if widget is None:
                    widget = widget_model(dashboard=current, widget_key=item["id"], **fields)
                    widget.full_clean()
                    widget.sudo(reason="dashboards.widget.create").save(using=using)
                    changed = True
                else:
                    dirty = [field for field, value in fields.items() if getattr(widget, field) != value]
                    if dirty:
                        for field in dirty:
                            setattr(widget, field, fields[field])
                        widget.full_clean()
                        widget.sudo(reason="dashboards.widget.update").save(using=using, update_fields=dirty)
                        changed = True
            for key, widget in existing_widgets.items():
                if key not in seen and not widget.is_archived:
                    widget.is_archived = True
                    widget.sudo(reason="dashboards.widget.archive").save(using=using, update_fields=["is_archived"])
                    changed = True
            if changed:
                current.columns = canonical["columns"]
                current.declaration_revision = declaration_revision
                current.sudo(reason="dashboards.save_snapshot").save(
                    using=using, update_fields=["columns", "declaration_revision", *dashboard_fields],
                )
            return current.with_actor(actor)


DashboardWidgetManager = AngeeHistoryManager.from_queryset(DashboardWidgetQuerySet)
