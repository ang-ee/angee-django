import * as React from "react";
import { useUiT } from "../i18n";
import { cn } from "../lib/cn";
import { useValueStable } from "../lib/use-value-stable";
import { Button } from "../ui/button";
import { Dialog } from "../ui/dialog";
import { Select } from "../ui/select";
import {
  GraphView,
  graphNodeStyle,
  type GraphViewConnection,
  type GraphViewNode,
  type GraphViewLayout,
  type GraphViewNodeStyle,
  type GraphViewPort,
  type GraphViewPosition,
  type GraphViewStatus,
} from "./GraphView";
import { layoutGraph, placeGraphNodeBeside } from "./graph-layout";

export interface GraphEditorNode extends Omit<GraphViewNode, "position" | "selected"> {
  ports: readonly GraphViewPort[];
}

export interface GraphEditorLink {
  from: string;
  port: string;
  to: string;
}

export type GraphEditorLayout = Readonly<Record<string, GraphViewPosition>>;

export interface GraphEditorSelection {
  nodes: readonly string[];
  link: GraphEditorLink | null;
}

export interface GraphEditorNodeAction {
  id: string;
  label: React.ReactNode;
  disabled?: boolean;
  /** The consumer owns the mutation, including creating duplicate nodes. */
  onSelect: (node: GraphEditorNode) => void;
}

export interface GraphEditorProps {
  nodes: readonly GraphEditorNode[];
  links: readonly GraphEditorLink[];
  /** Positions keyed by node ID. Omitted IDs receive automatic layout. */
  layout: GraphEditorLayout;
  /** Automatic layout and handle direction; display consumers default to top-to-bottom. */
  layoutOptions?: GraphViewLayout;
  readOnly?: boolean;
  selected?: GraphEditorSelection;
  onSelectionChange?: (selected: GraphEditorSelection) => void;
  canLink: (from: string, port: string, to: string) => boolean;
  /** Reconnecting dispatches onUnlink(previous), then onLink(next). */
  onLink: (link: GraphEditorLink) => void;
  onUnlink: (link: GraphEditorLink) => void;
  onDelete: (nodeIds: readonly string[]) => void;
  onLayoutChange: (layout: GraphEditorLayout) => void;
  /** Observe display positions, including automatic layout, without authoring a change. */
  onLayoutResolved?: (layout: GraphEditorLayout) => void;
  onInsertOnLink: (link: GraphEditorLink, position: GraphViewPosition) => void;
  onAddFromPort: (from: string, port: string, position: GraphViewPosition) => void;
  nodeActions?: (node: GraphEditorNode) => readonly GraphEditorNodeAction[];
  status?: Readonly<Record<string, GraphViewStatus | undefined>>;
  nodeStyles?: Readonly<Partial<Record<string, GraphViewNodeStyle>>>;
  ariaLabel?: string;
  className?: string;
}

const DEFAULT_NODE_STYLE = graphNodeStyle("var(--border-strong)", "neutral", { height: 100 });
const EMPTY_SELECTION: GraphEditorSelection = { nodes: [], link: null };

/** Controlled graph editing. Connection policy and every data mutation belong to the caller. */
export function GraphEditor({
  nodes, links, layout, layoutOptions, canLink, onLink, onUnlink, onDelete, onLayoutChange, onLayoutResolved,
  onInsertOnLink, onAddFromPort, nodeActions, status, nodeStyles, ariaLabel, className,
  readOnly = false, selected: controlledSelection, onSelectionChange,
}: GraphEditorProps): React.ReactElement {
  const t = useUiT();
  const resolvedLayoutOptions = useValueStable(layoutOptions);
  const [fitViewRequest, requestFit] = React.useReducer((value: number) => value + 1, 0);
  const [localSelection, setLocalSelection] = React.useState<GraphEditorSelection>(EMPTY_SELECTION);
  const selectionScope = JSON.stringify([nodes.map((node) => node.id).sort(), links.map(linkId).sort()]);
  const [previousSelectionScope, setPreviousSelectionScope] = React.useState(selectionScope);
  if (selectionScope !== previousSelectionScope) {
    const selectedLink = localSelection.link;
    setPreviousSelectionScope(selectionScope);
    setLocalSelection({
      nodes: localSelection.nodes.filter((id) => nodes.some((node) => node.id === id)),
      link: selectedLink && links.some((link) => linkId(link) === linkId(selectedLink)) ? selectedLink : null,
    });
  }
  const selection = controlledSelection ?? localSelection;
  // Native node and link changes can arrive in one event before React renders.
  const selectionIntent = React.useRef(selection);
  selectionIntent.current = selection;
  function select(patch: Partial<GraphEditorSelection>): void {
    const next = { ...selectionIntent.current, ...patch };
    selectionIntent.current = next;
    if (controlledSelection === undefined) setLocalSelection(next);
    onSelectionChange?.(next);
  }
  const [draft, setDraft] = React.useState<{ link: GraphEditorLink; previous?: GraphEditorLink } | null>(null);
  const selected = nodes.filter((node) => selection.nodes.includes(node.id));
  const selectedLink = links.find((link) => selection.link && linkId(link) === linkId(selection.link));
  const styles = React.useMemo(() => Object.fromEntries(nodes.map((node) => [
    node.kind, nodeStyles?.[node.kind] ?? DEFAULT_NODE_STYLE,
  ])), [nodeStyles, nodes]);
  const renderedEdges = React.useMemo(() => links.map((link) => ({
    id: linkId(link), source: link.from, target: link.to, sourceHandle: link.port,
    kind: "default", label: nodes.find((node) => node.id === link.from)?.ports.find((port) => port.id === link.port)?.label ?? link.port,
    selected: Boolean(selection.link && linkId(link) === linkId(selection.link)), meta: { link },
  })), [links, nodes, selection.link]);
  const dimensions = useValueStable(nodes.map((node) => {
    const style = styles[node.kind] ?? DEFAULT_NODE_STYLE;
    return { id: node.id, width: style.width, height: style.height };
  }));
  const topology = useValueStable(renderedEdges.map(({ id, source, target, kind }) => ({ id, source, target, kind })));
  const automatic = React.useMemo(() => layoutGraph({ nodes: dimensions, edges: topology, layout: resolvedLayoutOptions }), [dimensions, topology, resolvedLayoutOptions]);
  const resolvedLayout = React.useMemo(() => Object.fromEntries(dimensions.map((node) =>
    [node.id, layout[node.id] ?? automatic.positions.get(node.id)!])), [dimensions, layout, automatic]);
  React.useEffect(() => { onLayoutResolved?.(resolvedLayout); }, [onLayoutResolved, resolvedLayout]);
  const renderedNodes = nodes.map((node) => ({
    ...node, position: resolvedLayout[node.id], selected: selection.nodes.includes(node.id),
  }));

  function allowed(link: GraphEditorLink): boolean {
    return !readOnly && !links.some((entry) => linkId(entry) === linkId(link))
      && Boolean(nodes.find((node) => node.id === link.from)?.ports.some((port) => port.id === link.port))
      && nodes.some((node) => node.id === link.to) && canLink(link.from, link.port, link.to);
  }
  function connect(link: GraphEditorLink, previous?: GraphEditorLink): void {
    if (!allowed(link) || (previous && !links.some((entry) => linkId(entry) === linkId(previous)))) return;
    if (previous && linkId(previous) === linkId(link)) return;
    if (previous) onUnlink(previous);
    onLink(link);
  }
  function autoLayout(): void {
    if (!readOnly) {
      onLayoutChange(Object.fromEntries(automatic.positions));
      requestFit();
    }
  }
  function deleteSelection(): void {
    if (readOnly) return;
    if (selected.length) onDelete(selected.map((node) => node.id));
    if (selectedLink) onUnlink(selectedLink);
  }
  const nodeLabel = (id: string) => {
    const node = nodes.find((entry) => entry.id === id);
    return node?.ariaLabel ?? (typeof node?.title === "string" ? node.title : id);
  };
  const portLabel = (from: string, port: string) => nodes.find((node) => node.id === from)?.ports.find((entry) => entry.id === port)?.label ?? port;
  const linkVars = (link: GraphEditorLink) => ({ from: nodeLabel(link.from), port: portLabel(link.from, link.port), to: nodeLabel(link.to) });
  function suggestedPosition(from: string): GraphViewPosition | undefined {
    const node = nodes.find((entry) => entry.id === from);
    if (!node) return undefined;
    const size = styles[node.kind] ?? DEFAULT_NODE_STYLE;
    const occupied = nodes.map((entry) => ({ ...resolvedLayout[entry.id]!, ...(styles[entry.kind] ?? DEFAULT_NODE_STYLE) }));
    return placeGraphNodeBeside({ ...resolvedLayout[from]!, ...size }, size, occupied);
  }
  const source = nodes.find((node) => node.id === draft?.link.from);

  return (
    <section
      aria-label={ariaLabel ?? t("graph.editor")}
      className={cn("flex min-h-0 flex-col", className)}
      onKeyDown={(event) => {
        if (event.key !== "Delete" && event.key !== "Backspace") return;
        if (!(event.target instanceof Element)) return;
        if (event.target.closest("input, textarea, select, button, [contenteditable]:not([contenteditable=false]), [role=combobox]")) return;
        if (!event.target.closest("[data-graph-node-id], [data-graph-edge-id], .react-flow__pane")) return;
        event.preventDefault();
        deleteSelection();
      }}
    >
      <div className="flex shrink-0 flex-wrap items-center gap-2 border-b border-border-subtle p-2">
        <Button type="button" size="sm" onClick={autoLayout} disabled={readOnly || !nodes.length}>
          {t("graph.autoLayout")}
        </Button>
        <Button type="button" size="sm" onClick={deleteSelection} disabled={readOnly || (!selected.length && !selectedLink)}>
          {t("graph.deleteSelected")}
        </Button>
      </div>
      <GraphView
        className="min-h-64 flex-[3]"
        layout={resolvedLayoutOptions}
        fitViewRequest={fitViewRequest}
        nodes={renderedNodes}
        edges={renderedEdges}
        nodeStyles={styles}
        status={status}
        nodesDraggable={!readOnly}
        onNodesSelect={(entries) => select({ nodes: entries.map((node) => node.id) })}
        onEdgeSelect={(edge) => select({ link: edge?.meta?.link ?? null })}
        onNodesPositionChange={(positions) => { if (!readOnly) onLayoutChange({ ...resolvedLayout, ...positions }); }}
        onConnect={readOnly ? undefined : (connection) => connect(connectionLink(connection))}
        isValidConnection={(connection) => allowed(connectionLink(connection))}
        onReconnect={readOnly ? undefined : (edge, connection) => connect(connectionLink(connection), edge.meta?.link)}
      />
      <div className="min-h-0 max-h-36 shrink overflow-auto border-t border-border-subtle p-3">
        <h3 className="mb-2 text-13 font-semibold">{t("graph.connections")}</h3>
        <div className="mb-3 flex flex-wrap gap-2">
          {nodes.map((node) => (
            <Button
              type="button"
              key={node.id}
              size="sm"
              active={selected.some((entry) => entry.id === node.id)}
              aria-pressed={selected.some((entry) => entry.id === node.id)}
              aria-label={t("graph.selectNode", { node: nodeLabel(node.id) })}
              onClick={() => select({ nodes: [node.id], link: null })}
            >
              {node.title}
            </Button>
          ))}
        </div>
        {!selected.length ? <p className="text-13 text-fg-muted">{t("graph.empty")}</p> : null}
        {selected.map((node) => (
          <div key={node.id} className="mb-3 space-y-2">
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-13 font-semibold">{node.title}</span>
              {(nodeActions?.(node) ?? []).map((action) => (
                <Button
                  type="button"
                  key={action.id}
                  size="sm"
                  disabled={readOnly || action.disabled}
                  aria-label={t("graph.actionNode", { action: typeof action.label === "string" ? action.label : action.id, node: nodeLabel(node.id) })}
                  onClick={() => action.onSelect(node)}
                >
                  {action.label}
                </Button>
              ))}
              <Button
                type="button" size="sm" disabled={readOnly}
                aria-label={t("graph.deleteNode", { node: nodeLabel(node.id) })}
                onClick={() => onDelete([node.id])}
              >
                {t("graph.delete")}
              </Button>
            </div>
            {node.ports.map((port) => (
              <div key={port.id} className="flex flex-wrap items-center gap-2 text-13">
                <span>{port.label ?? port.id}</span>
                <Button
                  type="button"
                  size="sm"
                  disabled={readOnly}
                  aria-label={t("graph.connectPort", { port: port.label ?? port.id, node: nodeLabel(node.id) })}
                  onClick={() => setDraft({ link: { from: node.id, port: port.id, to: "" } })}
                >
                  {t("graph.connect")}
                </Button>
                <Button
                  type="button" size="sm" disabled={readOnly}
                  aria-label={t("graph.addPort", { port: port.label ?? port.id, node: nodeLabel(node.id) })}
                  onClick={() => {
                    const position = suggestedPosition(node.id);
                    if (!position) return;
                    onLayoutChange(resolvedLayout);
                    onAddFromPort(node.id, port.id, position);
                  }}
                >
                  {t("graph.add")}
                </Button>
              </div>
            ))}
          </div>
        ))}
        <ul className="space-y-2">
          {links.map((link) => (
            <li key={linkId(link)} className="flex flex-wrap items-center gap-2 text-13">
              <span>
                {nodes.find((node) => node.id === link.from)?.title ?? link.from}: {t("graph.link", {
                  port: nodes.find((node) => node.id === link.from)?.ports.find((port) => port.id === link.port)?.label ?? link.port,
                  target: nodeLabel(link.to),
                })}
              </span>
              <Button
                type="button" size="sm" disabled={readOnly}
                aria-label={t("graph.reconnectLink", linkVars(link))}
                onClick={() => setDraft({ link, previous: link })}
              >
                {t("graph.reconnect")}
              </Button>
              <Button
                type="button" size="sm" disabled={readOnly || !nodes.some((node) => node.id === link.from)}
                aria-label={t("graph.insertLink", linkVars(link))}
                onClick={() => {
                  const position = suggestedPosition(link.from);
                  if (!position) return;
                  onLayoutChange(resolvedLayout);
                  onInsertOnLink(link, position);
                }}
              >
                {t("graph.insert")}
              </Button>
              <Button
                type="button" size="sm" disabled={readOnly}
                aria-label={t("graph.deleteLink", linkVars(link))}
                onClick={() => onUnlink(link)}
              >
                {t("graph.delete")}
              </Button>
            </li>
          ))}
        </ul>
      </div>
      <Dialog.Root open={!readOnly && draft !== null} onOpenChange={(open) => { if (!open) setDraft(null); }}>
        <Dialog.Portal>
          <Dialog.Backdrop />
          <Dialog.Content size="sm">
            <Dialog.Header>
              <Dialog.Title>{draft?.previous ? t("graph.reconnect") : t("graph.connect")}</Dialog.Title>
            </Dialog.Header>
            <Dialog.Body className="space-y-3">
              <Select
                aria-label={t("graph.source")}
                value={draft?.link.from ?? ""}
                options={nodes.map((node) => ({ value: node.id, label: node.title }))}
                onValueChange={(from) => setDraft((current) => current ? {
                  ...current,
                  link: { from, port: nodes.find((node) => node.id === from)?.ports[0]?.id ?? "", to: "" },
                } : null)}
              />
              <Select
                aria-label={t("graph.port")}
                value={draft?.link.port ?? ""}
                options={source?.ports.map((port) => ({ value: port.id, label: port.label ?? port.id })) ?? []}
                onValueChange={(port) => setDraft((current) => current ? {
                  ...current, link: { ...current.link, port, to: "" },
                } : null)}
              />
              <Select
                aria-label={t("graph.target")}
                placeholder={t("graph.target")}
                value={draft?.link.to ?? ""}
                options={nodes.map((node) => ({
                  value: node.id, label: node.title, disabled: !draft || !allowed({ ...draft.link, to: node.id }),
                }))}
                onValueChange={(to) => setDraft((current) => current ? {
                  ...current, link: { ...current.link, to },
                } : null)}
              />
            </Dialog.Body>
            <Dialog.Footer>
              <Button type="button" onClick={() => setDraft(null)}>{t("graph.cancel")}</Button>
              <Button
                type="button"
                variant="primary"
                disabled={!draft || !allowed(draft.link)}
                onClick={() => {
                  if (!draft) return;
                  connect(draft.link, draft.previous);
                  setDraft(null);
                }}
              >
                {draft?.previous ? t("graph.reconnect") : t("graph.connect")}
              </Button>
            </Dialog.Footer>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </section>
  );
}

function linkId(link: GraphEditorLink): string {
  return JSON.stringify([link.from, link.port, link.to]);
}

function connectionLink(connection: GraphViewConnection): GraphEditorLink {
  return { from: connection.source, port: connection.sourceHandle ?? "", to: connection.target };
}
