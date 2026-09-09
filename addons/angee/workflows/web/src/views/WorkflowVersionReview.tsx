import * as React from "react";
import type { DocumentType } from "@angee/gql/console";
import {
  useAuthoredMutation,
  useAuthoredQuery,
  useSetAuthoredQueryData,
} from "@angee/refine";
import {
  Badge,
  Button,
  DialogBackdrop,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogPortal,
  DialogRoot,
  DialogTitle,
  EmptyState,
  errorMessage,
  ErrorBanner,
  GraphView,
  LoadingPanel,
  type GraphViewEdge,
  type GraphViewNode,
} from "@angee/ui";

import {
  RestoreWorkflowDefinitionDocument,
  WorkflowDefinitionDocument,
  WorkflowDefinitionComparisonDocument,
  WorkflowLaunchDocument,
} from "../documents.console";
import { useWorkflowsT } from "../i18n";
import { JsonBlock } from "./JsonBlock";
import { workflowNodeKind, workflowNodeStyles, type WorkflowGraphNodeKind } from "./graph-data";

const WORKFLOW_MODEL = "workflows.Workflow";

export function WorkflowVersionReview({ draftId, sourceId, sourceVersion, onRestored }: {
  draftId: string;
  sourceId: string;
  sourceVersion: number;
  onRestored: (draftId: string) => void;
}): React.ReactElement {
  const t = useWorkflowsT();
  const setAuthoredQueryData = useSetAuthoredQueryData();
  const [open, setOpen] = React.useState(false);
  const [selected, setSelected] = React.useState(0);
  const [snapshot, setSnapshot] = React.useState<ComparisonResult | null>(null);
  const [observedRevision, setObservedRevision] = React.useState<number | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const comparison = useAuthoredQuery(
    WorkflowDefinitionComparisonDocument,
    { workflow: draftId, source: sourceId },
    { models: [WORKFLOW_MODEL], enabled: open },
  );
  const draft = useAuthoredQuery(
    WorkflowLaunchDocument,
    { id: draftId },
    { models: [WORKFLOW_MODEL], enabled: open },
  );
  const [restore, restoreState] = useAuthoredMutation(RestoreWorkflowDefinitionDocument, {
    invalidateModels: [WORKFLOW_MODEL, "workflows.Step", "workflows.Edge"],
  });
  React.useEffect(() => {
    const result = comparison.data?.workflow_definition_comparison;
    if (open && result && snapshot == null) setSnapshot(result);
  }, [comparison.data, open, snapshot]);
  React.useEffect(() => {
    const revision = draft.data?.workflows_by_pk?.draft_revision;
    if (revision != null) setObservedRevision(revision);
  }, [draft.data?.workflows_by_pk?.draft_revision]);
  const currentRevision = observedRevision ?? draft.data?.workflows_by_pk?.draft_revision;
  const stale = snapshot != null && currentRevision != null && currentRevision !== snapshot.draft_revision;
  const change = snapshot?.changes[selected] ?? null;

  async function refresh(): Promise<void> {
    setError(null);
    try {
      const [comparisonResult, draftResult] = await Promise.all([
        comparison.refetch(),
        draft.refetch(),
      ]);
      const next = comparisonResult.data?.workflow_definition_comparison;
      if (!next) throw new Error(t("versions.unavailable"));
      const refreshedRevision = draftResult.data?.workflows_by_pk?.draft_revision ?? null;
      setObservedRevision(refreshedRevision);
      setSnapshot(next);
      setSelected(0);
    } catch (cause) {
      setError(errorMessage(cause, t("versions.unavailable")));
    }
  }

  async function restoreVersion(): Promise<void> {
    if (!snapshot || stale) return;
    setError(null);
    try {
      const data = await restore({ workflow: draftId, source: sourceId, expectedRevision: snapshot.draft_revision });
      const outcome = data?.restore_workflow_definition;
      if (!outcome || outcome.status !== "SUCCESS" || !outcome.snapshot) {
        throw new Error(outcome?.status === "STALE" ? t("versions.stale") : t("versions.restoreFailed"));
      }
      setAuthoredQueryData(
        WorkflowDefinitionDocument,
        { workflow: outcome.snapshot.workflow.id },
        { workflow_definition: outcome.snapshot },
      );
      setOpen(false);
      onRestored(outcome.snapshot.workflow.id);
    } catch (cause) {
      setError(errorMessage(cause, t("versions.restoreFailed")));
    }
  }

  return <>
    <Button type="button" size="sm" variant="secondary" onClick={() => { setSnapshot(null); setObservedRevision(null); setOpen(true); }}>
      {t("versions.compare")}
    </Button>
    <DialogRoot open={open} onOpenChange={setOpen}>
      <DialogPortal>
        <DialogBackdrop />
        <DialogContent size="lg">
          <DialogHeader>
            <DialogTitle>{t("versions.compareTitle", { version: sourceVersion })}</DialogTitle>
            <DialogDescription>{t("versions.compareDescription")}</DialogDescription>
          </DialogHeader>
          <DialogBody>
            {comparison.isFetching && !snapshot ? <LoadingPanel message={t("versions.loading")} /> : null}
            {!comparison.isFetching && !snapshot ? <EmptyState title={t("versions.unavailable")} /> : null}
            {snapshot ? <div className="grid gap-4">
              <div className="flex flex-wrap gap-2 text-xs">
                <Badge>{t("versions.versionLabel", { version: snapshot.source_version })}</Badge>
                <Badge>{t("versions.draftRevision", { revision: snapshot.draft_revision })}</Badge>
                {stale ? <Badge tone="warning">{t("versions.stale")}</Badge> : null}
              </div>
              <div className="grid grid-cols-2 gap-2 text-sm sm:grid-cols-3">
                <Count label={t("versions.stepsAdded")} value={snapshot.counts.steps_added} />
                <Count label={t("versions.stepsRemoved")} value={snapshot.counts.steps_removed} />
                <Count label={t("versions.stepsChanged")} value={snapshot.counts.steps_changed} />
                <Count label={t("versions.connectionsAdded")} value={snapshot.counts.connections_added} />
                <Count label={t("versions.connectionsRemoved")} value={snapshot.counts.connections_removed} />
                <Count label={t("versions.settingsChanged")} value={snapshot.counts.settings_changed} />
              </div>
              <div className="grid min-h-64 grid-cols-1 gap-3 md:grid-cols-[minmax(12rem,0.8fr)_minmax(0,1.2fr)]">
                <div className="overflow-auto border border-border-subtle">
                  {snapshot.changes.map((item, index) => <button key={`${item.kind}:${item.key}:${item.field ?? ""}`} type="button"
                    className="block w-full border-b border-border-subtle px-3 py-2 text-left text-sm hover:bg-inset"
                    aria-current={selected === index} onClick={() => setSelected(index)}>
                    <strong>{item.key}</strong><br /><span className="text-xs text-fg-muted">{item.kind} · {item.change}{item.field ? ` · ${item.field}` : ""}{item.presentation_only ? ` · ${t("versions.presentation")}` : ""}</span>
                  </button>)}
                  {!snapshot.changes.length ? <div className="p-3 text-sm text-fg-muted">{t("versions.noChanges")}</div> : null}
                </div>
                <div className="grid min-w-0 gap-3">
                  <ComparisonGraph comparison={snapshot} change={change} />
                  <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                    <section><h3 className="mb-1 text-xs font-semibold">{t("versions.before")}</h3><JsonBlock value={change?.before ?? null} /></section>
                    <section><h3 className="mb-1 text-xs font-semibold">{t("versions.after")}</h3><JsonBlock value={change?.after ?? null} /></section>
                  </div>
                </div>
              </div>
            </div> : null}
            <ErrorBanner description={error ?? comparison.error?.message ?? null} />
          </DialogBody>
          <DialogFooter>
            <Button type="button" variant="ghost" onClick={() => setOpen(false)}>{t("form.cancelReview")}</Button>
            <Button type="button" variant="secondary" onClick={() => void refresh()}>{t("versions.refresh")}</Button>
            <Button type="button" variant="danger" disabled={!snapshot || stale} loading={restoreState.fetching}
              onClick={() => void restoreVersion()}>{t("versions.restore")}</Button>
          </DialogFooter>
        </DialogContent>
      </DialogPortal>
    </DialogRoot>
  </>;
}

type ComparisonResult = DocumentType<typeof WorkflowDefinitionComparisonDocument>["workflow_definition_comparison"];

function Count({ label, value }: { label: string; value: number }): React.ReactElement {
  return <div className="rounded-6 border border-border-subtle p-2"><strong>{value}</strong><div className="text-xs text-fg-muted">{label}</div></div>;
}

function ComparisonGraph({ comparison, change }: { comparison: ComparisonResult; change: ComparisonResult["changes"][number] | null }): React.ReactElement {
  const t = useWorkflowsT();
  const removed = change?.kind === "step" && change.change === "removed";
  const sourceNodes = removed ? comparison.source_nodes : comparison.draft_nodes;
  const sourceEdges = removed ? comparison.source_edges : comparison.draft_edges;
  const nodes: GraphViewNode<WorkflowGraphNodeKind>[] = sourceNodes.map((node) => ({
    id: node.key,
    kind: workflowNodeKind(node.step_class),
    title: node.name || node.key,
    code: node.key,
    ariaLabel: `${node.name || node.key} · ${node.key}`,
    highlighted: node.is_entry,
    selected: change?.kind === "step" && change.key === node.key,
  }));
  const edges: GraphViewEdge<"default" | "condition">[] = sourceEdges.map((edge) => ({
    id: `${edge.source}:${edge.target}:${edge.condition}`,
    source: edge.source,
    target: edge.target,
    kind: edge.condition ? "condition" : "default",
    label: edge.condition || undefined,
    ariaLabel: `${edge.source} · ${edge.condition || "continues"} · ${edge.target}`,
    selected: change?.kind === "connection" && change.key === `${edge.source} → ${edge.target} [${edge.condition}]`,
  }));
  return <div className="h-56 min-h-0 overflow-hidden rounded-6 border border-border-subtle">
    <GraphView ariaLabel={t("versions.graph")} nodes={nodes} edges={edges} nodeStyles={workflowNodeStyles}
      nodesDraggable={false} fitViewOptions={{ padding: 0.18, maxZoom: 1 }} />
  </div>;
}
