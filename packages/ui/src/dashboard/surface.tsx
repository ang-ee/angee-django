import * as React from "react";
import { DndContext, useDraggable, type DragEndEvent } from "@dnd-kit/core";
import { useModelMetadata } from "@angee/metadata";
import { useDashboardRegistry } from "../runtime/runtime";
import { useDndKitSensors } from "../lib/dnd";
import { cn } from "../lib/cn";
import { Button } from "../ui/button";
import { Glyph } from "../chrome/Glyph";
import { Input } from "../ui/input";
import { ErrorBanner } from "../fragments/ErrorBanner";
import { InlineEmpty } from "../fragments/InlineEmpty";
import { LoadingPanel } from "../fragments/LoadingPanel";
import { PageHeader } from "../page/PageHeader";
import type {
  DashboardDefinition,
  DashboardLoadState,
  DashboardRegistry,
  DashboardSnapshot,
  DashboardStoreBinding,
  DashboardTarget,
  DashboardWidgetKind,
  WidgetSpec,
} from "./headless";
import { DASHBOARD_SCHEMA_VERSION, parseDashboardSnapshot } from "./headless";
import { firstDashboardSlot, moveDashboardRect, packDashboardLayout, projectDashboardLayout, resizeDashboardRect } from "./layout";
import { useDashboardWidgetData, type DashboardPageScope } from "./data";
import { useUnsavedChangesNavigationGuard } from "../views/form/use-unsaved-changes-navigation-guard";
import type { DashboardWidgetCatalogueEntry } from "./catalogue";
import { DashboardWidgetPickerDialog } from "./WidgetPickerDialog";
import { useDashboardT } from "./i18n";
import { titleCase } from "../lib/titleCase";

const ROW_HEIGHT = 56;
const GRID_GAP = 12;

export interface DashboardSurfaceProps {
  target: DashboardTarget;
  definition?: DashboardDefinition;
  fallbackSnapshot?: DashboardSnapshot;
  pageScope?: DashboardPageScope;
  className?: string;
  title?: string;
  headerActions?: React.ReactNode;
  toolbar?: React.ReactNode;
}

function snapshotForDefinition(definition: DashboardDefinition | undefined): DashboardSnapshot | null {
  if (!definition) return null;
  return parseDashboardSnapshot({
    schemaVersion: DASHBOARD_SCHEMA_VERSION,
    columns: definition.columns ?? 12,
    widgets: definition.widgets.map((widget) => ({
      ...widget,
      definitionRef: widget.definitionRef ?? widget.id,
    })),
  });
}

function definitionForTarget(registry: DashboardRegistry, target: DashboardTarget): DashboardDefinition | undefined {
  if (target.scope === "addon") return registry.definitions[target.key];
  if (target.scope === "resource") {
    const key = registry.resourceDefaults[target.key];
    return key ? registry.definitions[key] : undefined;
  }
  return undefined;
}

export function DashboardSurface(props: DashboardSurfaceProps): React.ReactElement {
  const registry = useDashboardRegistry();
  const definition = props.definition ?? definitionForTarget(registry, props.target);
  const baseline = props.fallbackSnapshot ?? snapshotForDefinition(definition);
  if (registry.store) {
    return <StoredDashboardSurface {...props} registry={registry} definition={definition} baseline={baseline} />;
  }
  if (!baseline) return <DashboardUnavailable />;
  return (
    <DashboardEditor
      {...props}
      registry={registry}
      definition={definition}
      committed={baseline}
      capabilities={{ canEdit: false, canReset: false }}
    />
  );
}

function StoredDashboardSurface(
  props: DashboardSurfaceProps & {
    registry: DashboardRegistry;
    definition?: DashboardDefinition;
    baseline: DashboardSnapshot | null;
  },
): React.ReactElement {
  const binding = props.registry.store!.useDashboard(props.target);
  const state = binding.state;
  const t = useDashboardT();
  if (state.status === "loading") return <LoadingPanel message={t("surface.loading")} />;
  if (state.status === "forbidden") return <DashboardUnavailable message={state.message ?? t("surface.forbidden")} />;
  if (state.status === "unavailable") return <DashboardUnavailable message={state.message ?? t("surface.unavailable")} />;
  if (state.status === "error") return <DashboardUnavailable message={state.error.message} />;
  if (state.status === "absent") {
    if (!props.baseline) return <DashboardUnavailable message={t("surface.absent")} />;
    return (
      <DashboardEditor
        {...props}
        committed={props.baseline}
        capabilities={{ canEdit: props.target.scope !== "personal", canReset: false }}
        onSave={async (snapshot) => {
          await binding.save({
            target: props.target,
            persistedId: null,
            expectedRevision: null,
            declarationRevision: props.definition?.revision,
            snapshot,
          });
        }}
      />
    );
  }
  return <ReadyDashboardSurface {...props} state={state} binding={binding} />;
}

function ReadyDashboardSurface(
  props: DashboardSurfaceProps & {
    registry: DashboardRegistry;
    definition?: DashboardDefinition;
    state: Extract<DashboardLoadState, { status: "ready" }>;
    binding: DashboardStoreBinding;
  },
): React.ReactElement {
  const { state } = props;
  return (
    <DashboardEditor
      {...props}
      title={props.title ?? state.name}
      committed={state.snapshot}
      capabilities={{ canEdit: state.capabilities.canEdit, canReset: state.capabilities.canReset }}
      onSave={async (snapshot) => {
        await props.binding.save({
          target: props.target,
          persistedId: state.persistedId,
          expectedRevision: state.revision,
          declarationRevision: props.definition?.revision,
          name: state.name,
          description: state.description,
          snapshot,
        });
      }}
      onReset={state.capabilities.canReset ? async () => {
        await props.binding.reset(props.target, state.persistedId, state.revision);
      } : undefined}
      onReload={props.binding.reload}
    />
  );
}

function DashboardUnavailable({ message }: { message?: string }): React.ReactElement {
  const t = useDashboardT();
  return <ErrorBanner className="m-4" description={message ?? t("surface.unavailable")} />;
}

function DashboardEditor({
  registry,
  definition,
  committed,
  capabilities,
  onSave,
  onReset,
  onReload,
  pageScope,
  className,
  title,
  headerActions,
  toolbar,
}: DashboardSurfaceProps & {
  registry: DashboardRegistry;
  definition?: DashboardDefinition;
  committed: DashboardSnapshot;
  capabilities: { canEdit: boolean; canReset: boolean };
  onSave?: (snapshot: DashboardSnapshot) => Promise<void>;
  onReset?: () => Promise<void>;
  onReload?: () => Promise<void>;
}): React.ReactElement {
  const t = useDashboardT();
  const [editing, setEditing] = React.useState(false);
  const [draft, setDraft] = React.useState(committed);
  const [pending, setPending] = React.useState(false);
  const [error, setError] = React.useState<Error | null>(null);
  const dirty = editing && JSON.stringify(draft) !== JSON.stringify(committed);
  const dirtyRef = React.useRef(dirty);
  React.useEffect(() => { dirtyRef.current = dirty; }, [dirty]);
  useUnsavedChangesNavigationGuard({
    isDirty: dirty,
    isDirtyNow: React.useCallback(() => dirtyRef.current, []),
    readOnly: !capabilities.canEdit,
  });
  React.useEffect(() => {
    if (!editing) setDraft(committed);
  }, [committed, editing]);

  const mutate = React.useCallback((widgets: readonly WidgetSpec[]) => {
    setDraft((current) => ({
      ...current,
      widgets: packDashboardLayout(widgets.filter((widget) => !widget.isArchived), current.columns)
        .concat(widgets.filter((widget) => widget.isArchived)),
    }));
  }, []);

  return (
    <section className={cn("flex min-h-0 flex-1 flex-col", className)}>
      <PageHeader
        density="compact"
        headingLevel={2}
        title={title ?? definition?.title ?? t("surface.defaultTitle")}
        actions={(
          <>
            {error ? (
              <>
            {onReload ? (
              <Button type="button" variant="ghost" size="sm" disabled={pending} onClick={() => {
                setPending(true);
                void onReload().then(() => {
                  setEditing(false);
                  setError(null);
                }).catch((cause) => setError(cause instanceof Error ? cause : new Error(String(cause)))).finally(() => setPending(false));
              }}>{t("surface.reload")}</Button>
            ) : null}
              </>
            ) : null}
        {headerActions}
        {editing ? (
          <>
            <AddWidgetButton snapshot={draft} registry={registry} pageScope={pageScope} onAdd={(widget) => mutate([...draft.widgets, widget])} />
            <Button type="button" variant="ghost" size="sm" disabled={pending} onClick={() => { setDraft(committed); setEditing(false); setError(null); }}>{t("surface.cancel")}</Button>
            <Button type="button" variant="primary" size="sm" disabled={pending || !onSave} onClick={() => {
              if (!onSave) return;
              setPending(true);
              setError(null);
              void onSave(parseDashboardSnapshot(draft)).then(() => setEditing(false)).catch((cause) => setError(cause instanceof Error ? cause : new Error(String(cause)))).finally(() => setPending(false));
            }}>{pending ? t("surface.saving") : t("surface.saveLayout")}</Button>
          </>
        ) : capabilities.canEdit ? (
          <Button type="button" variant="ghost" size="sm" onClick={() => setEditing(true)}><Glyph name="pencil" size={14} />{t("surface.edit")}</Button>
        ) : null}
        {!editing && capabilities.canReset && onReset ? (
          <Button type="button" variant="ghost" size="sm" disabled={pending} onClick={() => {
            setPending(true);
            void onReset().catch((cause) => setError(cause instanceof Error ? cause : new Error(String(cause)))).finally(() => setPending(false));
          }}>{t("surface.reset")}</Button>
        ) : null}
          </>
        )}
      />
      {toolbar}
      <ErrorBanner description={error?.message ?? null} />
      <DashboardGrid
        snapshot={draft}
        registry={registry}
        definition={definition}
        editing={editing}
        pageScope={pageScope}
        onWidgetsChange={mutate}
      />
    </section>
  );
}

function useResponsiveColumns(canonical: number): [React.RefObject<HTMLDivElement | null>, number] {
  const ref = React.useRef<HTMLDivElement>(null);
  const [columns, setColumns] = React.useState(canonical);
  React.useEffect(() => {
    const element = ref.current;
    if (!element || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(([entry]) => {
      const width = entry?.contentRect.width ?? 0;
      setColumns(Math.min(canonical, width < 640 ? 1 : width < 960 ? 6 : 12));
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, [canonical]);
  return [ref, columns];
}

function DashboardGrid({ snapshot, registry, definition, editing, pageScope, onWidgetsChange }: {
  snapshot: DashboardSnapshot;
  registry: DashboardRegistry;
  definition?: DashboardDefinition;
  editing: boolean;
  pageScope?: DashboardPageScope;
  onWidgetsChange: (widgets: readonly WidgetSpec[]) => void;
}): React.ReactElement {
  const t = useDashboardT();
  const sensors = useDndKitSensors(6);
  const [containerRef, responsiveColumns] = useResponsiveColumns(snapshot.columns);
  const columns = editing ? snapshot.columns : responsiveColumns;
  const active = snapshot.widgets.filter((widget) => !widget.isArchived);
  const projected = editing
    ? packDashboardLayout(active, columns)
    : columns < snapshot.columns
      ? packDashboardLayout(
          active.map((widget) => ({
            ...widget,
            w: responsiveWidgetWidth(widget, columns),
            h: responsiveWidgetHeight(widget, columns, snapshot.columns),
          })),
          columns,
        )
      : projectDashboardLayout(active, columns, snapshot.columns);
  const handleDragEnd = ({ active: dragged, delta }: DragEndEvent) => {
    const element = containerRef.current;
    const cellWidth = element ? (element.clientWidth - GRID_GAP * (columns - 1)) / columns : 0;
    const widget = projected.find((item) => item.id === String(dragged.id));
    if (!widget || !cellWidth) return;
    const moved = moveDashboardRect(
      active,
      widget.id,
      { x: widget.x + Math.round(delta.x / (cellWidth + GRID_GAP)), y: widget.y + Math.round(delta.y / (ROW_HEIGHT + GRID_GAP)) },
      columns,
    );
    onWidgetsChange([...moved, ...snapshot.widgets.filter((item) => item.isArchived)]);
  };
  return (
    <div ref={containerRef} className={cn("min-h-0 flex-1 overflow-auto p-3", editing && "min-w-[720px]")}>
      {projected.length === 0 ? <InlineEmpty icon="dashboard" label={t("surface.empty")} /> : (
        <DndContext sensors={sensors} onDragEnd={handleDragEnd}>
          <div className="grid min-h-0" style={{ gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))`, gridAutoRows: `${ROW_HEIGHT}px`, gap: GRID_GAP }}>
            {projected.map((widget) => (
              <DashboardCell
                key={widget.id}
                widget={widget}
                registry={registry}
                definition={definition}
                editing={editing}
                pageScope={pageScope}
                onArchive={() => onWidgetsChange(snapshot.widgets.map((item) => item.id === widget.id ? { ...item, isArchived: true } : item))}
                onUpdate={(patch) => onWidgetsChange(snapshot.widgets.map((item) => item.id === widget.id ? { ...item, ...patch } : item))}
                onMove={(dx, dy) => onWidgetsChange([
                  ...moveDashboardRect(active, widget.id, { x: widget.x + dx, y: widget.y + dy }, columns),
                  ...snapshot.widgets.filter((item) => item.isArchived),
                ])}
                onResize={(dw, dh) => onWidgetsChange([
                  ...resizeDashboardRect(active, widget.id, { w: widget.w + dw, h: widget.h + dh }, columns),
                  ...snapshot.widgets.filter((item) => item.isArchived),
                ])}
              />
            ))}
          </div>
        </DndContext>
      )}
    </div>
  );
}

function responsiveWidgetWidth(widget: WidgetSpec, columns: number): number {
  if (columns < 4 || widget.data.shape === "rows" || widget.data.shape === "none") return columns;
  return Math.ceil(columns / 2);
}

function responsiveWidgetHeight(widget: WidgetSpec, columns: number, canonicalColumns: number): number {
  if (columns >= canonicalColumns) return widget.h;
  if (widget.data.shape === "series") return Math.max(widget.h, 5);
  if (widget.data.shape === "rows") return Math.max(widget.h, 6);
  if (widget.data.shape === "none") return Math.max(widget.h, 4);
  return Math.max(widget.h, 2);
}

function DashboardCell({ widget, registry, definition, editing, pageScope, onArchive, onUpdate, onMove, onResize }: {
  widget: WidgetSpec;
  registry: DashboardRegistry;
  definition?: DashboardDefinition;
  editing: boolean;
  pageScope?: DashboardPageScope;
  onArchive: () => void;
  onUpdate: (patch: Partial<WidgetSpec>) => void;
  onMove: (dx: number, dy: number) => void;
  onResize: (dw: number, dh: number) => void;
}): React.ReactElement {
  const t = useDashboardT();
  const sourceResource = widget.data.shape === "none" ? "" : widget.data.source.resource;
  const sourceMetadata = useModelMetadata(sourceResource);
  const resourceLabel = sourceMetadata?.resource.modelName
    ? titleCase(sourceMetadata.resource.modelName)
    : titleCase(sourceResource.split(".").at(-1) ?? sourceResource);
  const visibleTitle = normalizeDashboardWidgetTitle(widget.title, sourceResource, resourceLabel);
  const drag = useDraggable({ id: widget.id, disabled: !editing });
  const transform = drag.transform;
  return (
    <article
      ref={drag.setNodeRef}
      tabIndex={editing ? 0 : undefined}
      aria-label={editing ? t("surface.layoutControls", { title: visibleTitle }) : undefined}
      onKeyDown={(event) => {
        if (!editing || event.target !== event.currentTarget) return;
        if (event.shiftKey && ["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(event.key)) {
          event.preventDefault();
          if (event.key === "ArrowLeft") onResize(-1, 0);
          if (event.key === "ArrowRight") onResize(1, 0);
          if (event.key === "ArrowUp") onResize(0, -1);
          if (event.key === "ArrowDown") onResize(0, 1);
          return;
        }
        if (["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(event.key)) {
          event.preventDefault();
          if (event.key === "ArrowLeft") onMove(-1, 0);
          if (event.key === "ArrowRight") onMove(1, 0);
          if (event.key === "ArrowUp") onMove(0, -1);
          if (event.key === "ArrowDown") onMove(0, 1);
        }
      }}
      className={cn("relative flex min-h-0 flex-col overflow-hidden rounded-8 border border-border-subtle bg-sheet shadow-sm", drag.isDragging && "z-10 opacity-90")}
      style={{
        gridColumn: `${widget.x + 1} / span ${widget.w}`,
        gridRow: `${widget.y + 1} / span ${widget.h}`,
        ...(transform ? { transform: `translate3d(${transform.x}px, ${transform.y}px, 0)` } : {}),
      }}
    >
      <header className="flex h-9 shrink-0 items-center gap-1 border-b border-border-subtle px-2">
        {editing ? <button type="button" className="cursor-grab rounded-4 p-1 text-fg-muted focus-visible:focus-ring" aria-label={t("surface.move", { title: visibleTitle })} {...drag.attributes} {...drag.listeners}><Glyph name="grip-vertical" fallbackName="more-vertical" size={14} /></button> : null}
        {editing ? (
          <Input
            size="sm"
            value={visibleTitle}
            onChange={(event) => onUpdate({ title: event.target.value })}
            aria-label={t("surface.widgetTitle")}
            className="min-w-0 flex-1"
          />
        ) : <h3 className="min-w-0 flex-1 truncate text-13 font-medium text-fg">{visibleTitle}</h3>}
        {editing ? (
          <>
            <button type="button" className="rounded-4 p-1 text-fg-muted focus-visible:focus-ring" aria-label={t("surface.moveLeft")} onClick={() => onMove(-1, 0)}><Glyph name="arrow-left" size={12} /></button>
            <button type="button" className="rounded-4 p-1 text-fg-muted focus-visible:focus-ring" aria-label={t("surface.moveRight")} onClick={() => onMove(1, 0)}><Glyph name="arrow-right" size={12} /></button>
            <button type="button" className="rounded-4 p-1 text-fg-muted focus-visible:focus-ring" aria-label={t("surface.wider")} onClick={() => onResize(1, 0)}><Glyph name="maximize-2" size={12} /></button>
            <button type="button" className="rounded-4 p-1 text-fg-muted focus-visible:focus-ring" aria-label={t("surface.removeWidget")} onClick={onArchive}><Glyph name="archive" fallbackName="x" size={12} /></button>
          </>
        ) : null}
      </header>
      <ResolvedWidget widget={widget} registry={registry} definition={definition} pageScope={pageScope} />
    </article>
  );
}

function ResolvedWidget({ widget, registry, definition, pageScope }: {
  widget: WidgetSpec;
  registry: DashboardRegistry;
  definition?: DashboardDefinition;
  pageScope?: DashboardPageScope;
}): React.ReactElement {
  const t = useDashboardT();
  const kind = registry.widgetKinds[widget.kind];
  if (!kind || kind.version !== widget.kindVersion || kind.shape !== widget.data.shape) {
    return <ErrorBanner className="m-3" description={t("surface.widgetKindUnavailable", { kind: widget.kind, version: widget.kindVersion })} />;
  }
  return <WidgetDataBody widget={widget} kind={kind} definition={definition} pageScope={pageScope} />;
}

function normalizeDashboardWidgetTitle(title: string, resource: string, resourceLabel: string): string {
  if (!resource) return title;
  if (title === resource) return resourceLabel;
  if (title.startsWith(`${resource} `)) return `${resourceLabel}${title.slice(resource.length)}`;
  return title;
}

function WidgetDataBody({ widget, kind, definition, pageScope }: {
  widget: WidgetSpec;
  kind: DashboardWidgetKind;
  definition?: DashboardDefinition;
  pageScope?: DashboardPageScope;
}): React.ReactElement {
  const t = useDashboardT();
  const data = useDashboardWidgetData(widget, pageScope);
  const Component = kind.Component;
  const Authored = definition?.authored?.[widget.id];
  return (
    <div className={cn(
      "flex min-h-0 flex-1 flex-col overflow-hidden",
      "px-3 pt-2 pb-1.5",
    )}>
      <div className={cn("min-h-0 flex-1", kind.shape === "rows" ? "overflow-auto" : "overflow-hidden")}>
        <Component spec={widget} data={data} authored={Authored ? <Authored /> : undefined} />
      </div>
      <footer className="flex shrink-0 items-center justify-end gap-2 pt-1 text-2xs text-fg-subtle">
        {pageScope && widget.data.shape !== "none" ? (
          <span>{pageScope.resource === widget.data.source.resource ? t("surface.pageFilters") : t("surface.independentSource")}</span>
        ) : null}
        <span>{data.live ? t("surface.live") : data.updatedAt ? t("surface.readAt", { time: new Date(data.updatedAt).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) }) : t("surface.manual")}</span>
        <button type="button" className="rounded-4 px-1 text-fg-muted hover:text-fg focus-visible:focus-ring" onClick={data.refetch}>{t("surface.refresh")}</button>
      </footer>
    </div>
  );
}

function AddWidgetButton({ snapshot, registry, pageScope, onAdd }: {
  snapshot: DashboardSnapshot;
  registry: DashboardRegistry;
  pageScope?: DashboardPageScope;
  onAdd: (widget: WidgetSpec) => void;
}): React.ReactElement {
  const t = useDashboardT();
  const [open, setOpen] = React.useState(false);
  const add = (entry: DashboardWidgetCatalogueEntry) => {
    const descriptor = registry.widgetKinds[entry.kind];
    if (!descriptor) return;
    const slot = firstDashboardSlot(
      snapshot.widgets.filter((widget) => !widget.isArchived),
      entry.size,
      snapshot.columns,
    );
    const id = globalThis.crypto?.randomUUID?.() ?? `widget-${Date.now()}`;
    const widget: WidgetSpec = {
      schemaVersion: 1,
      id,
      kind: entry.kind,
      kindVersion: descriptor.version,
      title: entry.title,
      data: entry.data,
      options: {},
      ...slot,
      ...entry.size,
      isArchived: false,
    };
    onAdd(widget);
  };
  return (
    <>
      <Button type="button" variant="ghost" size="sm" onClick={() => setOpen(true)}>
        <Glyph name="plus" size={14} />{t("surface.addWidget")}
      </Button>
      <DashboardWidgetPickerDialog
        open={open}
        onOpenChange={setOpen}
        onPick={add}
        preferredResource={pageScope?.resource}
        registry={registry}
      />
    </>
  );
}

export function DashboardCollectionSurface({
  resource,
  filter,
  className,
  onReturnToList,
}: {
  resource: string;
  filter?: DashboardPageScope["filter"];
  className?: string;
  onReturnToList?: () => void;
}): React.ReactElement {
  const t = useDashboardT();
  const metadata = useModelMetadata(resource);
  const registry = useDashboardRegistry();
  const declaredKey = registry.resourceDefaults[resource];
  const definition = declaredKey ? registry.definitions[declaredKey] : undefined;
  const fallback = React.useMemo<DashboardSnapshot | undefined>(() => {
    if (definition || !metadata?.resource) return undefined;
    const count: WidgetSpec = {
      schemaVersion: 1,
      id: "count",
      kind: "stat",
      kindVersion: 1,
      title: t("surface.total"),
      data: { shape: "value", source: { resource, measure: { op: "count" } } },
      options: {},
      x: 0, y: 0, w: 3, h: 2,
      isArchived: false,
    };
    const axes = Object.values(metadata.resource.query.axes).filter((axis) => {
      const field = metadata.resource.query.fields[axis.field];
      return axis.server && field?.kind !== "json" && field?.kind !== "list" && field?.kind !== "object";
    }).slice(0, 2);
    const charts = axes.map<WidgetSpec>((axis, index) => ({
      schemaVersion: 1,
      id: `group-${axis.field}`,
      kind: "bar",
      kindVersion: 1,
      title: t("surface.byField", { field: titleCase(axis.field) }),
      data: {
        shape: "series",
        source: {
          resource,
          groups: [{
            field: axis.field,
            ...(axis.kind === "date" && axis.extractions.some(({ name }) => name === "month")
              ? { granularity: "month" }
              : {}),
          }],
          measure: { op: "count" },
          limit: 8,
        },
      },
      options: {},
      x: 3 + index * 4, y: 0, w: 4, h: 4,
      isArchived: false,
    }));
    return parseDashboardSnapshot({ schemaVersion: 1, columns: 12, widgets: [count, ...charts] });
  }, [definition, metadata?.resource, resource, t]);
  const resourceLabel = metadata?.resource.modelName
    ? titleCase(metadata.resource.modelName)
    : titleCase(resource.split(".").at(-1) ?? resource);
  return (
    <DashboardSurface
      target={{ scope: "resource", key: resource }}
      definition={definition}
      fallbackSnapshot={fallback}
      pageScope={{ resource, filter }}
      className={className}
      title={definition?.title ?? t("surface.resourceDashboard", { resource: resourceLabel })}
      headerActions={onReturnToList ? (
        <Button type="button" variant="ghost" size="sm" onClick={onReturnToList}>
          <Glyph name="list" size={14} />{t("surface.list")}
        </Button>
      ) : undefined}
    />
  );
}
