import * as React from "react";

import { Badge } from "../ui/badge";
import { Button } from "../ui/button";
import { StatusDot } from "../ui/status-icon";
import { SegmentedControl } from "../ui/toggle-group";
import { InlineEmpty } from "./InlineEmpty";
import { MetricStrip, type MetricTileValue } from "./MetricStrip";
import { RailPanel } from "./RailPanel";

type QueryStateSchema = Record<string, unknown>;
type QueryStateValues<TSchema extends QueryStateSchema> = Partial<TSchema> &
  Record<string, unknown>;

export type DataLensVisual = "graph" | "chart" | "metrics" | "map" | "tree";

export interface DataLensNode {
  id: string;
  label: React.ReactNode;
  value?: number;
  parentId?: string | null;
  tone?: "brand" | "info" | "success" | "warning" | "danger";
}

export interface DataLensProjection {
  title?: React.ReactNode;
  summary?: React.ReactNode;
  nodes?: readonly DataLensNode[];
  metrics?: readonly MetricTileValue[];
  empty?: React.ReactNode;
}

export interface DataLensProps<
  TRow = unknown,
  TSchema extends QueryStateSchema = QueryStateSchema,
> {
  visual: DataLensVisual;
  title?: React.ReactNode;
  rows?: readonly TRow[];
  project?: (
    rows: readonly TRow[],
    state: QueryStateValues<TSchema>,
  ) => DataLensProjection;
  selectionKey?: string;
  onSelect?: (node: DataLensNode) => void;
  className?: string;
}

const dataLensOptions: { value: DataLensVisual; label: string }[] = [
  { value: "graph", label: "Graph" },
  { value: "chart", label: "Chart" },
  { value: "metrics", label: "Metrics" },
  { value: "map", label: "Map" },
  { value: "tree", label: "Tree" },
];

export function DataLens<
  TRow = unknown,
  TSchema extends QueryStateSchema = QueryStateSchema,
>({
  visual,
  title,
  rows: rowsOverride,
  project,
  selectionKey = "selection",
  onSelect,
  className,
}: DataLensProps<TRow, TSchema>): React.ReactElement {
  const rows = rowsOverride ?? [];
  const [activeVisual, setActiveVisual] = React.useState<DataLensVisual>(visual);
  const [state, setState] = React.useState<QueryStateValues<TSchema>>(
    {} as QueryStateValues<TSchema>,
  );

  React.useEffect(() => {
    setActiveVisual(visual);
  }, [visual]);

  const projection = React.useMemo(
    () => project?.(rows, state) ?? defaultProjection(rows),
    [project, rows, state],
  );

  function selectNode(node: DataLensNode): void {
    onSelect?.(node);
    setState((old) => ({
      ...old,
      [selectionKey]: [node.id],
    }));
  }

  return (
    <RailPanel
      actions={
        <SegmentedControl
          aria-label="Lens visual"
          onValueChange={setActiveVisual}
          options={dataLensOptions}
          size="xs"
          value={activeVisual}
        />
      }
      className={className}
      count={projection.summary}
      empty={projection.empty ?? "No projection data"}
      title={title ?? projection.title ?? "Lens"}
    >
      {renderVisual(activeVisual, projection, selectNode)}
    </RailPanel>
  );
}

function defaultProjection(rows: readonly unknown[]): DataLensProjection {
  return {
    metrics: [{ label: "Rows", value: rows.length.toLocaleString() }],
    nodes: rows.slice(0, 8).map((row, index) => ({
      id: rowId(row, index),
      label: rowLabel(row, index),
    })),
    summary: rows.length.toLocaleString(),
  };
}

function renderVisual(
  visual: DataLensVisual,
  projection: DataLensProjection,
  onSelect: (node: DataLensNode) => void,
): React.ReactElement {
  const nodes = projection.nodes ?? [];

  switch (visual) {
    case "metrics":
      return renderMetrics(projection);
    case "chart":
      return <ChartProjection nodes={nodes} onSelect={onSelect} />;
    case "map":
      return <NodeList label="Map" nodes={nodes} onSelect={onSelect} />;
    case "tree":
      return <TreeProjection nodes={nodes} onSelect={onSelect} />;
    case "graph":
    default:
      return <NodeList label="Graph" nodes={nodes} onSelect={onSelect} />;
  }
}

function renderMetrics(projection: DataLensProjection): React.ReactElement {
  const metrics = projection.metrics ?? [];
  if (metrics.length === 0) {
    return <InlineEmpty icon="activity" label={projection.empty ?? "No metrics"} />;
  }
  return <MetricStrip metrics={metrics} />;
}

function NodeList({
  label,
  nodes,
  onSelect,
}: {
  label: string;
  nodes: readonly DataLensNode[];
  onSelect: (node: DataLensNode) => void;
}): React.ReactElement {
  if (nodes.length === 0) {
    return <InlineEmpty icon="list" label="No nodes" />;
  }

  return (
    <div className="space-y-2">
      <div className="text-2xs font-semibold uppercase text-fg-muted">{label}</div>
      <div className="space-y-1">
        {nodes.map((node) => (
          <Button
            className="w-full justify-between"
            key={node.id}
            onClick={() => onSelect(node)}
            type="button"
            variant="secondary"
          >
            <span className="truncate">{node.label}</span>
            {node.value !== undefined ? (
              <Badge tone={node.tone ?? "brand"}>{node.value}</Badge>
            ) : null}
          </Button>
        ))}
      </div>
    </div>
  );
}

function ChartProjection({
  nodes,
  onSelect,
}: {
  nodes: readonly DataLensNode[];
  onSelect: (node: DataLensNode) => void;
}): React.ReactElement {
  if (nodes.length === 0) {
    return <InlineEmpty icon="activity" label="No chart data" />;
  }

  const max = Math.max(1, ...nodes.map((node) => node.value ?? 1));

  return (
    <div className="space-y-2">
      {nodes.map((node) => (
        <button
          className="grid w-full gap-1 rounded-md px-2 py-1.5 text-left hover:bg-inset focus-visible:focus-ring"
          key={node.id}
          onClick={() => onSelect(node)}
          type="button"
        >
          <span className="truncate text-13 font-medium text-fg">
            {node.label}
          </span>
          <span className="h-2 overflow-hidden rounded-full bg-inset">
            <span
              className="block h-full rounded-full bg-brand"
              style={{ width: `${((node.value ?? 1) / max) * 100}%` }}
            />
          </span>
        </button>
      ))}
    </div>
  );
}

function TreeProjection({
  nodes,
  onSelect,
}: {
  nodes: readonly DataLensNode[];
  onSelect: (node: DataLensNode) => void;
}): React.ReactElement {
  if (nodes.length === 0) {
    return <InlineEmpty icon="list" label="No tree data" />;
  }

  return (
    <div className="space-y-1">
      {nodes.map((node) => (
        <button
          className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-13 hover:bg-inset focus-visible:focus-ring"
          key={node.id}
          onClick={() => onSelect(node)}
          type="button"
        >
          <StatusDot tone={node.tone ?? "brand"} size="sm" />
          <span className="min-w-0 flex-1 truncate text-fg">{node.label}</span>
          {node.parentId ? <Badge>{node.parentId}</Badge> : null}
        </button>
      ))}
    </div>
  );
}

function rowId(row: unknown, index: number): string {
  if (row && typeof row === "object" && "id" in row) {
    return String(row.id);
  }
  return String(index);
}

function rowLabel(row: unknown, index: number): React.ReactNode {
  if (row && typeof row === "object" && "label" in row) {
    return String(row.label);
  }
  if (row && typeof row === "object" && "name" in row) {
    return String(row.name);
  }
  return `Row ${index + 1}`;
}
