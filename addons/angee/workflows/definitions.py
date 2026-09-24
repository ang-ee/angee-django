"""Atomic, transport-free workflow definition edit commands."""

from __future__ import annotations

import copy
from collections.abc import Iterable, Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from django.core.exceptions import ValidationError
from django.db import transaction

from angee.base.identity import public_id_of
from angee.base.scoping import system_queryset
from angee.workflows.attempts import json_values_equal
from angee.workflows.graph import (
    GraphDiagnostic,
    GraphIdentity,
    GraphInputSource,
    GraphLocation,
    GraphLocationKind,
    GraphMapBodyCandidate,
    WorkflowGraph,
)

if TYPE_CHECKING:
    from angee.workflows.managers import DefinitionWriteSession, WorkflowQuerySet


def declaration_changed(
    instance: Any,
    persisted: Any | None,
    *,
    fields: Iterable[str],
    update_fields: Iterable[str] | None,
) -> bool:
    """Compare only persisted declaration values, using native FK storage names."""

    if persisted is None:
        return True
    updated = None if update_fields is None else set(update_fields)
    for name in fields:
        attname = instance._meta.get_field(name).attname
        if updated is None or name in updated or attname in updated:
            if getattr(instance, attname) != getattr(persisted, attname):
                return True
    return False


@dataclass(frozen=True, slots=True)
class EndpointRef:
    """Reference exactly one persisted or command-created step."""

    existing_id: int | None = None
    client_key: str | None = None


@dataclass(frozen=True, slots=True)
class NodeCreate:
    client_key: str
    fields: dict[str, Any]


@dataclass(frozen=True, slots=True)
class NodePatch:
    identity: int
    fields: dict[str, Any]
    requested_id: str | None = None


@dataclass(frozen=True, slots=True)
class NodeDelete:
    identity: int
    requested_id: str | None = None


@dataclass(frozen=True, slots=True)
class EdgeCreate:
    client_key: str
    source: EndpointRef
    target: EndpointRef
    fields: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class EdgePatch:
    identity: int
    fields: dict[str, Any] = field(default_factory=dict)
    source: EndpointRef | None = None
    target: EndpointRef | None = None
    requested_id: str | None = None


@dataclass(frozen=True, slots=True)
class EdgeDelete:
    identity: int
    requested_id: str | None = None


@dataclass(frozen=True, slots=True)
class DefinitionEdit:
    workflow: dict[str, Any] = field(default_factory=dict)
    node_creates: tuple[NodeCreate, ...] = ()
    node_patches: tuple[NodePatch, ...] = ()
    node_deletes: tuple[NodeDelete, ...] = ()
    edge_creates: tuple[EdgeCreate, ...] = ()
    edge_patches: tuple[EdgePatch, ...] = ()
    edge_deletes: tuple[EdgeDelete, ...] = ()


@dataclass(frozen=True, slots=True)
class Correlation:
    client_key: str
    identity: int


@dataclass(frozen=True, slots=True)
class DefinitionResult:
    revision: int
    nodes: tuple[Correlation, ...]
    edges: tuple[Correlation, ...]
    readiness: tuple[GraphDiagnostic, ...]


@dataclass(frozen=True, slots=True)
class DefinitionSnapshot:
    revision: int
    workflow: Any
    nodes: tuple[Any, ...]
    edges: tuple[Any, ...]
    readiness: tuple[GraphDiagnostic, ...]


@dataclass(frozen=True, slots=True)
class PublicationResult:
    revision: int
    publication: Any
    created: bool


@dataclass(frozen=True, slots=True)
class DefinitionChange:
    """One semantic or presentation difference between two saved definitions."""

    kind: str
    change: str
    key: str
    field: str | None
    before: Any
    after: Any
    presentation_only: bool = False


@dataclass(frozen=True, slots=True)
class DefinitionComparison:
    """A coherent comparison of an immutable version and one saved draft."""

    source: Any
    draft: Any
    draft_revision: int
    source_definition: dict[str, Any]
    draft_definition: dict[str, Any]
    changes: tuple[DefinitionChange, ...]


@dataclass(frozen=True, slots=True)
class DefinitionRestoreResult:
    """The new canonical draft snapshot after restoring an immutable version."""

    source: Any
    snapshot: DefinitionSnapshot


@dataclass(frozen=True, slots=True)
class DefinitionInputSources:
    revision: int
    target: GraphIdentity
    sources: tuple[GraphInputSource, ...]
    readiness: tuple[GraphDiagnostic, ...]


@dataclass(frozen=True, slots=True)
class DefinitionMapBodyCandidates:
    revision: int
    owner: GraphIdentity
    candidates: tuple[GraphMapBodyCandidate, ...]
    readiness: tuple[GraphDiagnostic, ...]


class StaleDefinitionError(Exception):
    """Raised when a command does not target the locked draft revision."""

    def __init__(self, *, expected: int, current: int) -> None:
        self.expected = expected
        self.current = current
        super().__init__(f"Draft revision is stale: expected {expected}, current {current}.")


class DefinitionEditError(Exception):
    """Raised with all command or structural diagnostics before persistence."""

    def __init__(self, diagnostics: tuple[GraphDiagnostic, ...]) -> None:
        self.diagnostics = diagnostics
        super().__init__(f"Workflow definition edit is structurally invalid: {diagnostics!r}")


class DefinitionReadinessError(Exception):
    """Raised with every readiness diagnostic blocking publication."""

    def __init__(self, diagnostics: tuple[GraphDiagnostic, ...]) -> None:
        self.diagnostics = diagnostics
        super().__init__(f"Workflow definition is not ready: {diagnostics!r}")


class WorkflowDefinitionManagerMixin:
    """Own atomic workflow definition commands on a Workflow manager."""

    if TYPE_CHECKING:
        model: type[Any]

        def get(self, **kwargs: Any) -> Any: ...
        def with_action(self, action: str) -> WorkflowQuerySet: ...
        def current_published_for(self, workflow: Any) -> Any | None: ...
        def _definition_caller(self, workflow: Any) -> AbstractContextManager[None]: ...
        def _definition_read(self, workflow_id: int) -> AbstractContextManager[Any]: ...
        def _definition_write(
            self,
            workflow_ids: Iterable[int],
            *,
            session: DefinitionWriteSession | None = None,
            _allow_status_transition: bool = False,
        ) -> AbstractContextManager[DefinitionWriteSession]: ...

    def install_definition(
        self,
        model: type[Any],
        declarations: Mapping[str, Any],
        *,
        ledger_model: type[Any],
        source_addon: str,
        source_path: str,
    ) -> dict[str, Any]:
        """Reconcile one native-cleaned, exactly source-owned definition facet.

        Import-export owns identity, coercion, row outcomes and ledger upserts.
        This owner composes snapshots and checked edits, including omission.
        Incident edges must be explicitly removed by their own facet before a
        step can be omitted; a facet never cascades another source's declaration.

        The head's draft_revision is a compare-and-swap counter, not a load
        number. Each changed facet advances it through its own definition edit;
        one load changing workflow scalars, steps and edges advances it three
        times. Callers compare or carry the returned revision, never its delta.
        """

        steps = self.model._meta.get_field("steps").related_model
        edges = self.model._meta.get_field("edges").related_model
        if model not in {self.model, steps, edges}:
            raise ValidationError("Unsupported workflow definition facet.")
        with transaction.atomic():
            owned = ledger_model._default_manager.filter(
                source_addon=source_addon, source_path=source_path, target_model=model._meta.label,
            )
            omitted = list(owned.exclude(xref__in=declarations))
            removed = {row.pk: row for ledger in omitted if (row := ledger.target_instance()) is not None}
            candidates = list(declarations.values())
            if any(not isinstance(row, model) for row in candidates):
                raise ValidationError("A workflow declaration must contain its native cleaned instance.")
            retained_ids = {row.pk for row in candidates if not row._state.adding}
            if len(retained_ids) != sum(not row._state.adding for row in candidates):
                raise ValidationError("Two resource xrefs identify the same workflow definition row.")
            removed = {pk: row for pk, row in removed.items() if pk not in retained_ids}
            targets = (*candidates, *removed.values())
            target_ids = [public_id_of(row) for row in targets if not row._state.adding]
            foreign = ledger_model._default_manager.filter(
                target_model=model._meta.label, target_id__in=target_ids,
            ).exclude(source_addon=source_addon, source_path=source_path)
            if foreign.exists():
                raise ValidationError("A workflow definition row is owned by another resource contribution.")
            head_ids = {
                row.pk if model is self.model else row.workflow_id
                for row in targets if model is not self.model or not row._state.adding
            }
            with self._definition_write(head_ids):
                persisted: dict[str, Any] = {}
                if model is self.model:
                    for xref, row in declarations.items():
                        if row.error_workflow is not None:
                            # An earlier buffered native row now has its PK;
                            # reassign the cached relation before comparing IDs.
                            row.error_workflow = row.error_workflow
                        if row._state.adding:
                            row.save()
                        else:
                            snapshot = self.definition_snapshot(row)
                            if row.key != snapshot.workflow.key:
                                # The model owns one-time key backfill and rejects renames.
                                row.save(update_fields={"key"})
                            patch = self._installation_patch(
                                row, snapshot.workflow, self.model.editable_declaration_fields
                            )
                            if patch:
                                self.apply_definition(
                                    row, expected_revision=snapshot.revision, edit=DefinitionEdit(workflow=patch),
                                )
                        persisted[xref] = self.get(pk=row.pk)
                    for row in removed.values():
                        if (
                            row.steps.exists() or row.edges.exists()
                            or row.triggers.exists()
                            or row.error_for_workflows.exists()
                        ):
                            raise ValidationError("Omitted workflow still has contributions; remove its facets first.")
                        row.delete()
                else:
                    for head_id in sorted(head_ids):
                        head = self.get(pk=head_id)
                        current = {xref: row for xref, row in declarations.items() if row.workflow_id == head_id}
                        omitted_rows = [row for row in removed.values() if row.workflow_id == head_id]
                        persisted.update(self._install_definition_children(
                            head, model, current, omitted_rows,
                        ))
                # Upserts remain exclusively in AngeeResource.after_save_instance.
                owned.filter(pk__in=[ledger.pk for ledger in omitted]).delete()
                return persisted

    @staticmethod
    def _installation_patch(candidate: Any, stored: Any, names: frozenset[str]) -> dict[str, Any]:
        """Compare native cleaned values without repeating import field coercion."""

        return {
            name: getattr(candidate, name)
            for name in sorted(names)
            if not json_values_equal(
                getattr(candidate, candidate._meta.get_field(name).attname),
                getattr(stored, stored._meta.get_field(name).attname),
            )
        }

    def _install_definition_children(
        self, head: Any, model: type[Any], declarations: Mapping[str, Any], omitted: list[Any]
    ) -> dict[str, Any]:
        snapshot = self.definition_snapshot(head)
        is_step = model is self.model._meta.get_field("steps").related_model
        saved = {row.pk: row for row in (snapshot.nodes if is_step else snapshot.edges)}
        node_creates: list[NodeCreate] = []
        node_patches: list[NodePatch] = []
        edge_creates: list[EdgeCreate] = []
        edge_patches: list[EdgePatch] = []
        for xref, row in declarations.items():
            if not row._state.adding and row.pk not in saved:
                raise ValidationError(f"{xref}: a resource definition cannot move to another workflow.")
            names = model.editable_declaration_fields
            wanted = {name: getattr(row, name) for name in names}
            if is_step:
                if row._state.adding:
                    node_creates.append(NodeCreate(xref, wanted))
                else:
                    patch = self._installation_patch(row, saved[row.pk], names)
                    if patch:
                        node_patches.append(NodePatch(row.pk, patch))
            else:
                source, target = EndpointRef(existing_id=row.source_id), EndpointRef(existing_id=row.target_id)
                if row._state.adding:
                    edge_creates.append(EdgeCreate(xref, source, target, wanted))
                else:
                    old = saved[row.pk]
                    patch = self._installation_patch(row, old, names)
                    changed = old.source_id != row.source_id or old.target_id != row.target_id
                    if patch or changed:
                        edge_patches.append(EdgePatch(
                            row.pk, patch, source if changed else None, target if changed else None,
                        ))
        removed_ids = {row.pk for row in omitted}
        if is_step and any(edge.source_id in removed_ids or edge.target_id in removed_ids for edge in snapshot.edges):
            raise ValidationError("Omitted step still has incident edges; remove those edge declarations first.")
        edit = DefinitionEdit(
            node_creates=tuple(node_creates), node_patches=tuple(node_patches),
            node_deletes=tuple(NodeDelete(pk) for pk in sorted(removed_ids)) if is_step else (),
            edge_creates=tuple(edge_creates), edge_patches=tuple(edge_patches),
            edge_deletes=() if is_step else tuple(EdgeDelete(pk) for pk in sorted(removed_ids)),
        )
        created: dict[str, int] = {}
        if any((node_creates, node_patches, edge_creates, edge_patches, removed_ids)):
            result = self.apply_definition(head, expected_revision=snapshot.revision, edit=edit)
            created = {item.client_key: item.identity for item in (result.nodes if is_step else result.edges)}
        rows = model._default_manager.in_bulk(
            [created.get(xref, row.pk) for xref, row in declarations.items()],
        )
        return {xref: rows[created.get(xref, row.pk)] for xref, row in declarations.items()}

    def definition_snapshot(self, workflow: Any) -> DefinitionSnapshot:
        """Read one coherent revision and definition under its lineage lock."""

        with self._definition_caller(workflow):
            return self._definition_snapshot(workflow)

    def definition_graph(self, workflow: Any) -> WorkflowGraph:
        """Read one authorized workflow and its exact owned definition rows.

        Step and Edge read authority derives from their Workflow owner.  Prove
        that owner once, then capture the owned rows through the internal
        definition seam so graph inspection does not recursively re-evaluate
        the same relationship policy for every row.
        """

        with self._definition_caller(workflow):
            self.with_action("read").get(pk=workflow.pk)
            with self._definition_read(workflow.pk) as locked:
                return self._owned_definition_graph(locked)

    @staticmethod
    def _owned_definition_rows(workflow: Any) -> tuple[tuple[Any, ...], tuple[Any, ...]]:
        """Return all rows owned by one already-authorized definition."""

        step_model = workflow.steps.model
        edge_model = workflow.edges.model
        nodes = tuple(
            system_queryset(step_model, lock=None)
            .filter(workflow_id=workflow.pk)
            .order_by("key", "pk")
        )
        edges = tuple(
            system_queryset(edge_model, lock=None)
            .filter(workflow_id=workflow.pk)
            .select_related("source", "target")
            .order_by("pk")
        )
        return nodes, edges

    def _owned_definition_graph(self, workflow: Any) -> WorkflowGraph:
        nodes, edges = self._owned_definition_rows(workflow)
        return WorkflowGraph.from_rows(workflow, nodes, edges)

    def _definition_snapshot(self, workflow: Any) -> DefinitionSnapshot:
        projected = self.with_action("read").with_lineage_projection().get(pk=workflow.pk)
        with self._definition_read(workflow.pk) as locked:
            nodes, edges = self._owned_definition_rows(locked)
            readiness = WorkflowGraph.from_rows(locked, nodes, edges).diagnostics()
            return DefinitionSnapshot(locked.draft_revision, projected, nodes, edges, readiness)

    def apply_definition(
        self,
        workflow: Any,
        *,
        expected_revision: int,
        edit: DefinitionEdit,
    ) -> DefinitionResult:
        """Apply one compare-and-swap edit and return its committed revision."""

        self._validate_expected_revision(workflow, expected_revision)
        with self._definition_caller(workflow):
            return self._apply_definition(workflow, expected_revision=expected_revision, edit=edit)

    def definition_input_sources(
        self,
        workflow: Any,
        *,
        expected_revision: int,
        edit: DefinitionEdit,
        target: EndpointRef,
    ) -> DefinitionInputSources:
        """Project source metadata from one unsaved definition without persisting it."""

        self._validate_expected_revision(workflow, expected_revision)
        with self._definition_caller(workflow):
            with self._definition_read(workflow.pk) as locked:
                if locked.draft_revision != expected_revision:
                    raise StaleDefinitionError(expected=expected_revision, current=locked.draft_revision)
                state = _DefinitionState(self, locked, edit, action="read")
                state.preflight()
                target_identity = state.target_identity(target)
                graph = state.graph()
                return DefinitionInputSources(
                    locked.draft_revision,
                    target_identity,
                    graph.input_sources(target_identity),
                    graph.diagnostics(),
                )

    def definition_map_body_candidates(
        self,
        workflow: Any,
        *,
        expected_revision: int,
        edit: DefinitionEdit,
        owner: EndpointRef,
    ) -> DefinitionMapBodyCandidates:
        """Project Map bodies from one exact unsaved definition without persisting it."""

        self._validate_expected_revision(workflow, expected_revision)
        with self._definition_caller(workflow):
            with self._definition_read(workflow.pk) as locked:
                if locked.draft_revision != expected_revision:
                    raise StaleDefinitionError(expected=expected_revision, current=locked.draft_revision)
                state = _DefinitionState(self, locked, edit, action="read")
                state.preflight()
                owner_identity = state.target_identity(owner)
                graph = state.graph()
                return DefinitionMapBodyCandidates(
                    locked.draft_revision,
                    owner_identity,
                    graph.map_body_candidates(owner_identity),
                    graph.diagnostics(),
                )

    def publish_definition(self, workflow: Any, *, expected_revision: int) -> PublicationResult:
        """Publish the exact saved revision or return its identical publication."""

        self._validate_expected_revision(workflow, expected_revision)
        with self._definition_caller(workflow):
            with self._definition_write((workflow.pk,)) as session:
                draft = self.get(pk=workflow.pk)
                if draft.draft_revision != expected_revision:
                    raise StaleDefinitionError(expected=expected_revision, current=draft.draft_revision)
                diagnostics = self._owned_definition_graph(draft).diagnostics()
                if diagnostics:
                    raise DefinitionReadinessError(diagnostics)
                current = self.current_published_for(draft)
                if current is not None and draft._definition_signature() == current._definition_signature():
                    projected = self.with_action("read").with_lineage_projection().get(pk=current.pk)
                    return PublicationResult(draft.draft_revision, projected, False)
                published = draft.publish(session=session)
                projected = self.with_action("read").with_lineage_projection().get(pk=published.pk)
                return PublicationResult(draft.draft_revision, projected, True)

    def compare_definition(self, workflow: Any, source: Any) -> DefinitionComparison:
        """Compare two exact saved lineage definitions under one coherent lock."""

        with self._definition_caller(workflow):
            self.with_action("read").get(pk=workflow.pk)
            self.with_action("read").get(pk=source.pk)
            with self._definition_write(
                (workflow.pk, source.pk), _allow_status_transition=True
            ) as session:
                by_id = {row.pk: row for row in session.rows}
                draft = by_id[workflow.pk]
                locked_source = by_id[source.pk]
                _validate_version_source(draft, locked_source)
                source_definition = locked_source._definition_signature()
                draft_definition = draft._definition_signature()
                return DefinitionComparison(
                    source=locked_source,
                    draft=draft,
                    draft_revision=draft.draft_revision,
                    source_definition=source_definition,
                    draft_definition=draft_definition,
                    changes=_definition_changes(source_definition, draft_definition),
                )

    def restore_definition(
        self,
        workflow: Any,
        source: Any,
        *,
        expected_revision: int,
    ) -> DefinitionRestoreResult:
        """Replace one exact saved draft with an immutable lineage version."""

        self._validate_expected_revision(workflow, expected_revision)
        with self._definition_caller(workflow):
            self.with_action("write").get(pk=workflow.pk)
            self.with_action("read").get(pk=source.pk)
            with self._definition_write(
                (workflow.pk, source.pk), _allow_status_transition=True
            ) as session:
                by_id = {row.pk: row for row in session.rows}
                draft = by_id[workflow.pk]
                locked_source = by_id[source.pk]
                _validate_version_source(draft, locked_source)
                if draft.draft_revision != expected_revision:
                    raise StaleDefinitionError(expected=expected_revision, current=draft.draft_revision)
                draft.edges.all().delete(session=session)
                draft.steps.all().delete(session=session)
                fields = self.model.editable_declaration_fields
                for field_name in sorted(fields):
                    attname = draft._meta.get_field(field_name).attname
                    setattr(draft, attname, copy.deepcopy(getattr(locked_source, attname)))
                draft.save(update_fields=[*sorted(fields), "updated_at"], session=session)
                locked_source._copy_definition_to(draft, session=session)
                projected_revision = session.revision(draft.pk, draft.draft_revision)
                draft.draft_revision = projected_revision
                nodes = tuple(draft.steps.order_by("key", "pk"))
                edges = tuple(draft.edges.select_related("source", "target").order_by("pk"))
                snapshot = DefinitionSnapshot(
                    projected_revision,
                    draft,
                    nodes,
                    edges,
                    WorkflowGraph.from_rows(draft, nodes, edges).diagnostics(),
                )
                result = DefinitionRestoreResult(source=locked_source, snapshot=snapshot)
            return result

    @staticmethod
    def _validate_expected_revision(workflow: Any, expected_revision: int) -> None:
        if type(expected_revision) is int and 0 <= expected_revision <= 2_147_483_647:
            return
        raise DefinitionEditError(
            (
                GraphDiagnostic(
                    "revision_invalid",
                    "Expected revision must be a non-negative 32-bit integer.",
                    GraphLocation("workflow", GraphIdentity(existing_id=workflow.pk), "draft_revision"),
                ),
            )
        )

    def _apply_definition(self, workflow: Any, *, expected_revision: int, edit: DefinitionEdit) -> DefinitionResult:
        result: DefinitionResult
        with self._definition_write((workflow.pk,)) as session:
            locked = self.get(pk=workflow.pk)
            if locked.draft_revision != expected_revision:
                raise StaleDefinitionError(expected=expected_revision, current=locked.draft_revision)
            state = _DefinitionState(self, locked, edit)
            state.preflight()
            node_correlations, edge_correlations = state.persist(session=session)
            revision = session.revision(locked.pk, locked.draft_revision)
            readiness = self._owned_definition_graph(locked).diagnostics()
            result = DefinitionResult(revision, tuple(node_correlations), tuple(edge_correlations), readiness)
        return result


class _DefinitionState:
    """One proposed definition inside its manager-owned locked transaction."""

    def __init__(self, manager: Any, workflow: Any, edit: DefinitionEdit, *, action: str = "write") -> None:
        self.manager = manager
        self.workflow = workflow
        self.edit = edit
        self.step_model = workflow.steps.model
        self.edge_model = workflow.edges.model
        self.workflow_fields = dict(edit.workflow)
        self.nodes = {row.pk: row for row in workflow.steps.with_action(action).order_by("pk")}
        self.edges = {
            row.pk: row
            for row in (
                workflow.edges.with_action(action).select_related("source", "target").order_by("pk")
            )
        }
        self.original_nodes = dict(self.nodes)
        self.original_edges = dict(self.edges)
        self.original_edge_signatures = {identity: _edge_signature(row) for identity, row in self.edges.items()}
        self.created_nodes: dict[str, Any] = {}
        self.created_edges: dict[str, Any] = {}
        self.diagnostics: list[GraphDiagnostic] = []

    def preflight(self) -> None:
        all_node_count = system_queryset(self.step_model).filter(workflow_id=self.workflow.pk).count()
        all_edge_count = system_queryset(self.edge_model).filter(workflow_id=self.workflow.pk).count()
        if len(self.nodes) != all_node_count or len(self.edges) != all_edge_count:
            self._command(
                "workflow",
                GraphIdentity(existing_id=self.workflow.pk),
                "definition",
                "reference_invalid",
                "Definition rows are missing or unavailable.",
            )
        self._validate_command_shape()
        self._validate_workflow_relations()
        if self.diagnostics:
            raise DefinitionEditError(tuple(self.diagnostics))
        self._build_proposed_rows()
        self._validate_constraint_order()
        self._validate_models()
        graph = self.graph()
        self.diagnostics.extend(graph.structural_diagnostics())
        if self.diagnostics:
            raise DefinitionEditError(tuple(self.diagnostics))

    def graph(self) -> WorkflowGraph:
        """Return the one graph projection shared by preview and persistence preflight."""

        return WorkflowGraph.from_rows(
            self.workflow,
            self.nodes.values(),
            self.edges.values(),
            identity=lambda row: getattr(row, "_definition_identity", GraphIdentity(existing_id=row.pk)),
            owner_identity=lambda row: GraphIdentity(existing_id=row.workflow_id),
        )

    def target_identity(self, reference: EndpointRef) -> GraphIdentity:
        exact = (reference.existing_id is None) != (reference.client_key is None)
        if not exact:
            raise DefinitionEditError(
                (
                    GraphDiagnostic(
                        "reference_invalid",
                        "Target is missing or unavailable.",
                        GraphLocation("node", GraphIdentity(), "identity"),
                    ),
                )
            )
        if reference.existing_id is not None and reference.existing_id in self.nodes:
            return GraphIdentity(existing_id=reference.existing_id)
        if reference.client_key is not None and reference.client_key in self.created_nodes:
            return GraphIdentity(client_key=reference.client_key)
        identity = GraphIdentity(existing_id=reference.existing_id, client_key=reference.client_key)
        raise DefinitionEditError(
            (
                GraphDiagnostic(
                    "reference_invalid",
                    "Target is missing or unavailable.",
                    GraphLocation("node", identity, "identity"),
                ),
            )
        )

    def _validate_workflow_relations(self) -> None:
        if "error_workflow" not in self.workflow_fields or self.workflow_fields["error_workflow"] is None:
            return
        related = self.workflow_fields["error_workflow"]
        related_pk = getattr(related, "pk", None)
        available = self.manager.with_action("read").filter(pk=related_pk).first()
        if available is None:
            self._command(
                "workflow",
                GraphIdentity(existing_id=self.workflow.pk),
                "error_workflow",
                "reference_invalid",
                "Error workflow is missing or unavailable.",
            )
            return
        self.workflow_fields["error_workflow"] = available

    def _validate_constraint_order(self) -> None:
        """Reject existing edge swaps that immediate uniqueness cannot persist."""

        original = {signature: identity for identity, signature in self.original_edge_signatures.items()}
        proposed = {identity: _edge_signature(row) for identity, row in self.edges.items() if isinstance(identity, int)}
        for identity, signature in proposed.items():
            owner = original.get(signature)
            if owner is not None and owner != identity and proposed.get(owner) != signature:
                self._command(
                    "edge",
                    GraphIdentity(existing_id=identity),
                    "identity",
                    "edge_swap_unsupported",
                    "Atomic swaps of existing edge identities are not supported.",
                )

    def persist(self, *, session: DefinitionWriteSession) -> tuple[list[Correlation], list[Correlation]]:
        workflow_fields = set(self.workflow_fields)
        if workflow_fields:
            for name, value in self.workflow_fields.items():
                setattr(self.workflow, name, copy.deepcopy(value))
            self.workflow.save(update_fields=workflow_fields, session=session)

        deleted_node_ids = {item.identity for item in self.edit.node_deletes}
        incident = [
            edge
            for edge in self.original_edges.values()
            if edge.source_id in deleted_node_ids or edge.target_id in deleted_node_ids
        ]
        for edge in sorted(incident, key=lambda row: row.pk or 0):
            if edge.pk:
                edge.delete(session=session)
        for edge_delete in self.edit.edge_deletes:
            edge = self.original_edges.get(edge_delete.identity)
            if edge is not None and edge not in incident:
                edge.delete(session=session)
        for node_delete in self.edit.node_deletes:
            self.original_nodes[node_delete.identity].delete(session=session)

        node_results: list[Correlation] = []
        for node_patch in self.edit.node_patches:
            row = self.nodes[node_patch.identity]
            row.save(update_fields=set(node_patch.fields), session=session)
        for node_create in self.edit.node_creates:
            row = self.created_nodes[node_create.client_key]
            row.save(session=session)
            node_results.append(Correlation(node_create.client_key, row.pk))

        edge_results: list[Correlation] = []
        for edge_patch in self.edit.edge_patches:
            row = self.edges[edge_patch.identity]
            fields = set(edge_patch.fields)
            if edge_patch.source is not None:
                row.source = self._persisted_endpoint(edge_patch.source)
                fields.add("source")
            if edge_patch.target is not None:
                row.target = self._persisted_endpoint(edge_patch.target)
                fields.add("target")
            row.save(update_fields=fields, session=session)
        for edge_create in self.edit.edge_creates:
            row = self.created_edges[edge_create.client_key]
            row.source = self._persisted_endpoint(edge_create.source)
            row.target = self._persisted_endpoint(edge_create.target)
            row.save(session=session)
            edge_results.append(Correlation(edge_create.client_key, row.pk))
        return node_results, edge_results

    def _validate_command_shape(self) -> None:
        self._fields(
            "workflow",
            GraphIdentity(existing_id=self.workflow.pk),
            self.workflow_fields,
            self.workflow.editable_declaration_fields,
        )
        for node_create in self.edit.node_creates:
            self._fields(
                "node",
                GraphIdentity(client_key=node_create.client_key),
                node_create.fields,
                self.step_model.editable_declaration_fields,
            )
        for node_patch in self.edit.node_patches:
            self._fields(
                "node", _edit_identity(node_patch), node_patch.fields, self.step_model.editable_declaration_fields
            )
        for edge_create in self.edit.edge_creates:
            self._fields(
                "edge",
                GraphIdentity(client_key=edge_create.client_key),
                edge_create.fields,
                self.edge_model.editable_declaration_fields,
            )
        for edge_patch in self.edit.edge_patches:
            self._fields(
                "edge", _edit_identity(edge_patch), edge_patch.fields,
                self.edge_model.editable_declaration_fields,
            )
        self._unique("node", "client_key", [item.client_key for item in self.edit.node_creates], client=True)
        self._unique("edge", "client_key", [item.client_key for item in self.edit.edge_creates], client=True)
        self._unique(
            "workflow",
            "client_key",
            [node.client_key for node in self.edit.node_creates] + [edge.client_key for edge in self.edit.edge_creates],
            client=True,
        )
        self._unique(
            "node",
            "identity",
            [node.identity for node in self.edit.node_patches] + [node.identity for node in self.edit.node_deletes],
        )
        self._unique(
            "edge",
            "identity",
            [edge.identity for edge in self.edit.edge_patches] + [edge.identity for edge in self.edit.edge_deletes],
        )
        node_edits: tuple[NodePatch | NodeDelete, ...] = (*self.edit.node_patches, *self.edit.node_deletes)
        for node_edit in node_edits:
            if node_edit.identity not in self.nodes:
                self._command(
                    "node",
                    _edit_identity(node_edit),
                    "identity",
                    "reference_invalid",
                    "Node is missing or unavailable.",
                )
        edge_edits: tuple[EdgePatch | EdgeDelete, ...] = (*self.edit.edge_patches, *self.edit.edge_deletes)
        for edge_edit in edge_edits:
            if edge_edit.identity not in self.edges:
                self._command(
                    "edge",
                    _edit_identity(edge_edit),
                    "identity",
                    "reference_invalid",
                    "Edge is missing or unavailable.",
                )
        key_patches = {item.identity: item.fields["key"] for item in self.edit.node_patches if "key" in item.fields}
        key_owners = {row.key: identity for identity, row in self.nodes.items()}
        for identity, desired in key_patches.items():
            owner = key_owners.get(desired) if isinstance(desired, str) else None
            if owner is not None and owner != identity and owner in key_patches:
                self._command(
                    "node",
                    GraphIdentity(existing_id=identity),
                    "key",
                    "key_swap_unsupported",
                    "Atomic swaps of existing step keys are not supported.",
                )
        deleted_nodes = {item.identity for item in self.edit.node_deletes}
        incident_edges = {
            identity
            for identity, edge in self.edges.items()
            if edge.source_id in deleted_nodes or edge.target_id in deleted_nodes
        }
        for edge_patch in self.edit.edge_patches:
            if edge_patch.identity in incident_edges:
                self._command(
                    "edge",
                    GraphIdentity(existing_id=edge_patch.identity),
                    "identity",
                    "edit_conflict",
                    "An edge removed with its node cannot also be patched.",
                )
        for edge_create in self.edit.edge_creates:
            for field_name, reference in (("source", edge_create.source), ("target", edge_create.target)):
                self._validate_endpoint(
                    reference,
                    location=GraphIdentity(client_key=edge_create.client_key),
                    field=field_name,
                )
        for edge_patch in self.edit.edge_patches:
            for field_name, optional_reference in (("source", edge_patch.source), ("target", edge_patch.target)):
                if optional_reference is not None:
                    self._validate_endpoint(
                        optional_reference,
                        location=_edit_identity(edge_patch),
                        field=field_name,
                    )

    def _build_proposed_rows(self) -> None:
        for name, value in self.workflow_fields.items():
            setattr(self.workflow, name, copy.deepcopy(value))
        for node_patch in self.edit.node_patches:
            row = self.nodes[node_patch.identity]
            for name, value in node_patch.fields.items():
                setattr(row, name, copy.deepcopy(value))
        for node_delete in self.edit.node_deletes:
            self.nodes.pop(node_delete.identity)
        for node_create in self.edit.node_creates:
            row = self.step_model(workflow=self.workflow, **copy.deepcopy(node_create.fields))
            row._definition_identity = GraphIdentity(client_key=node_create.client_key)
            self.created_nodes[node_create.client_key] = row
            self.nodes[node_create.client_key] = row
        for row in self.nodes.values():
            row._definition_identity = getattr(row, "_definition_identity", GraphIdentity(existing_id=row.pk))
        deleted_nodes = {item.identity for item in self.edit.node_deletes}
        for identity, row in tuple(self.edges.items()):
            if (
                identity in {item.identity for item in self.edit.edge_deletes}
                or row.source_id in deleted_nodes
                or row.target_id in deleted_nodes
            ):
                self.edges.pop(identity)
        for edge_patch in self.edit.edge_patches:
            row = self.edges[edge_patch.identity]
            for name, value in edge_patch.fields.items():
                setattr(row, name, copy.deepcopy(value))
            if edge_patch.source is not None:
                row.source = self._endpoint(edge_patch.source)
            if edge_patch.target is not None:
                row.target = self._endpoint(edge_patch.target)
        for edge_create in self.edit.edge_creates:
            row = self.edge_model(
                workflow=self.workflow,
                source=self._endpoint(edge_create.source),
                target=self._endpoint(edge_create.target),
                **copy.deepcopy(edge_create.fields),
            )
            row._definition_identity = GraphIdentity(client_key=edge_create.client_key)
            self.created_edges[edge_create.client_key] = row
            self.edges[edge_create.client_key] = row
        for row in self.edges.values():
            row._definition_identity = getattr(row, "_definition_identity", GraphIdentity(existing_id=row.pk))

    def _validate_models(self) -> None:
        try:
            self.workflow.full_clean(validate_unique=False, validate_constraints=False)
        except ValidationError as error:
            for path, messages in error.message_dict.items():
                for message in messages:
                    self._command(
                        "workflow",
                        GraphIdentity(existing_id=self.workflow.pk),
                        path,
                        "field_invalid",
                        message,
                    )
        self._validate_model_rows("node", self.nodes.values())
        self._validate_model_rows("edge", self.edges.values())

    def _validate_model_rows(self, kind: GraphLocationKind, rows: Iterable[Any]) -> None:
        for row in rows:
            try:
                row.full_clean(
                    exclude={"source", "target"} if kind == "edge" else None,
                    validate_unique=False,
                    validate_constraints=False,
                )
            except ValidationError as error:
                identity = row._definition_identity
                for path, messages in error.message_dict.items():
                    for message in messages:
                        self._command(kind, identity, path, "field_invalid", message)

    def _endpoint(self, reference: EndpointRef) -> Any:
        if reference.existing_id is not None:
            return self.nodes[reference.existing_id]
        assert reference.client_key is not None, "validated endpoints have one identity"
        return self.created_nodes[reference.client_key]

    def _persisted_endpoint(self, reference: EndpointRef) -> Any:
        return self._endpoint(reference)

    def _validate_endpoint(self, reference: EndpointRef, *, location: GraphIdentity, field: str) -> None:
        exact = (reference.existing_id is None) != (reference.client_key is None)
        deleted = {item.identity for item in self.edit.node_deletes}
        found = (
            reference.existing_id in self.nodes and reference.existing_id not in deleted
            if reference.existing_id is not None
            else reference.client_key in {item.client_key for item in self.edit.node_creates}
        )
        if not exact or not found:
            self._command("edge", location, field, "reference_invalid", "Endpoint is missing or unavailable.")

    def _fields(
        self,
        kind: GraphLocationKind,
        identity: GraphIdentity,
        values: dict[str, Any],
        allowed: frozenset[str],
    ) -> None:
        model = {"workflow": self.workflow._meta.model, "node": self.step_model, "edge": self.edge_model}[kind]
        for name in sorted(set(values) - allowed):
            self._command(
                kind,
                identity,
                name,
                "field_unsupported",
                f"Field {name!r} cannot be edited.",
            )
        for name, value in values.items():
            if name in allowed and value is None and not model._meta.get_field(name).null:
                self._command(
                    kind,
                    identity,
                    name,
                    "field_invalid",
                    "This field cannot be null.",
                )

    def _unique(self, kind: GraphLocationKind, field: str, values: list[Any], *, client: bool = False) -> None:
        seen: set[Any] = set()
        for value in values:
            if not value or value in seen:
                self._command(
                    kind,
                    GraphIdentity(client_key=str(value)) if client else GraphIdentity(existing_id=value),
                    field,
                    "identity_duplicate",
                    f"{field} must be request-unique and non-blank.",
                )
            seen.add(value)

    def _command(self, kind: GraphLocationKind, identity: GraphIdentity, field: str, code: str, message: str) -> None:
        self.diagnostics.append(GraphDiagnostic(code, message, GraphLocation(kind, identity, field)))


def _edge_signature(edge: Any) -> tuple[GraphIdentity, GraphIdentity, str]:
    """Return the domain identity tuple behind the immediate edge constraint."""

    source = getattr(edge.source, "_definition_identity", GraphIdentity(existing_id=edge.source_id))
    target = getattr(edge.target, "_definition_identity", GraphIdentity(existing_id=edge.target_id))
    return source, target, str(edge.condition)


def _validate_version_source(draft: Any, source: Any) -> None:
    """Require one mutable head and one real immutable publication in its lineage."""

    if draft.published_from_id is not None or str(draft.status) != "draft":
        raise ValidationError({"workflow": "Restore and comparison require the editable lineage head."})
    if source.published_from_id != draft.pk or str(source.status) not in {"published", "archived"}:
        raise ValidationError({"source": "Select a published version from this workflow lineage."})


def _definition_changes(before: dict[str, Any], after: dict[str, Any]) -> tuple[DefinitionChange, ...]:
    """Describe saved definition changes using stable domain identities."""

    changes: list[DefinitionChange] = []
    for field_name in sorted(set(before["workflow"]) | set(after["workflow"])):
        old = before["workflow"].get(field_name)
        new = after["workflow"].get(field_name)
        if not json_values_equal(old, new):
            changes.append(DefinitionChange("settings", "changed", "workflow", field_name, old, new))

    before_steps = {item["key"]: item for item in before["steps"]}
    after_steps = {item["key"]: item for item in after["steps"]}
    for key in sorted(before_steps.keys() | after_steps.keys()):
        old_step = before_steps.get(key)
        new_step = after_steps.get(key)
        if old_step is None:
            changes.append(DefinitionChange("step", "added", key, None, None, new_step))
            continue
        if new_step is None:
            changes.append(DefinitionChange("step", "removed", key, None, old_step, None))
            continue
        for field_name in sorted(set(old_step) | set(new_step)):
            if field_name == "key":
                continue
            old = old_step.get(field_name)
            new = new_step.get(field_name)
            if not json_values_equal(old, new):
                changes.append(
                    DefinitionChange(
                        "step",
                        "changed",
                        key,
                        field_name,
                        old,
                        new,
                        presentation_only=field_name in {"name", "position"},
                    )
                )

    def edge_key(item: dict[str, Any]) -> tuple[str, str, str]:
        return str(item["source"]), str(item["target"]), str(item["condition"])

    before_edges = {edge_key(item): item for item in before["edges"]}
    after_edges = {edge_key(item): item for item in after["edges"]}
    for key in sorted(before_edges.keys() | after_edges.keys()):
        label = f"{key[0]} → {key[1]} [{key[2]}]"
        if key not in before_edges:
            changes.append(DefinitionChange("connection", "added", label, None, None, after_edges[key]))
        elif key not in after_edges:
            changes.append(DefinitionChange("connection", "removed", label, None, before_edges[key], None))
    return tuple(changes)


def _edit_identity(item: NodePatch | NodeDelete | EdgePatch | EdgeDelete) -> GraphIdentity:
    return GraphIdentity(
        existing_id=item.identity if item.identity >= 0 else None,
        requested_id=item.requested_id,
    )
