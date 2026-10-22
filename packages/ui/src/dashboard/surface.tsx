import * as React from "react";
import { DndContext, useDraggable, type DragEndEvent } from "@dnd-kit/core";
import { useModelMetadata } from "@angee/metadata";
import { useAppRuntime, useResourceRoute, useRouteHref } from "../runtime/runtime";
import { useDashboardRegistry } from "../runtime/runtime";
import { useDndKitSensors } from "../lib/dnd";
import { cn } from "../lib/cn";
import { Button } from "../ui/button";
import { Card, CardContent, CardHeader } from "../ui/card";
import { ActionMenu, ActionTrigger } from "../toolbars/ActionMenu";
import { Glyph } from "../chrome/Glyph";
import { Input } from "../ui/input";
import { NavLink } from "../ui/nav-link";
import { ErrorBanner } from "../fragments/ErrorBanner";
import { InlineEmpty } from "../fragments/InlineEmpty";
import { SurfaceHeader, type SurfaceHeaderProps } from "../fragments/SurfaceHeader";
import { PageBody } from "../page/PageBody";
import { Skeleton, SkeletonStatus, SkeletonText } from "../ui/skeleton";
import type {
  DashboardDefinition,
  DashboardPageScope,
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
import { useDashboardWidgetData } from "./data";
import { useUnsavedChangesNavigationGuard } from "../views/form/use-unsaved-changes-navigation-guard";
import type { DashboardWidgetCatalogueEntry } from "./catalogue";
import { DashboardWidgetPickerDialog } from "./WidgetPickerDialog";
import { useDashboardT } from "./i18n";
import { titleCase } from "../lib/titleCase";
import { SectionHeading } from "../views/form/SectionHeading";
import { Filter, createResourceViewState, resourceViewStateToSearch } from "../views/resource/resource-view-model";

const ROW_HEIGHT = 56;
const GRID_GAP = 16;

export interface DashboardSurfaceProps {
  target: DashboardTarget;
  definition?: DashboardDefinition;
  fallbackSnapshot?: DashboardSnapshot;
  pageScope?: DashboardPageScope;
  className?: string;
  title?: string;
  subtitle?: React.ReactNode;
  headerDensity?: SurfaceHeaderProps["density"];
  headingLevel?: SurfaceHeaderProps["headingLevel"];
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

/** The committed layout presented by a ready dashboard, including a newer declaration.
 * @kind null
 * @reason Codec and transport infrastructure: translates runtime data and addresses.
 */
export function visibleDashboardSnapshot(
  registry: DashboardRegistry,
  target: DashboardTarget,
  state: Extract<DashboardLoadState, { status: "ready" }>,
  definition = definitionForTarget(registry, target),
  baseline = snapshotForDefinition(definition),
): DashboardSnapshot {
  return definition && baseline && definition.revision !== state.declarationRevision
    ? baseline : state.snapshot;
}

/**
 * @kind surface
 */
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
  if (state.status === "loading") {
    return (
      <DashboardSkeleton
        className={props.className}
        label={t("surface.loading")}
        toolbar={props.toolbar}
      />
    );
  }
  if (state.status === "forbidden") return <DashboardUnavailable message={state.message ?? t("surface.forbidden")} />;
  if (state.status === "unavailable") return <DashboardUnavailable message={state.message ?? t("surface.unavailable")} />;
  if (state.status === "error") return <DashboardUnavailable message={state.error.message} />;
  if (state.status === "absent") {
    if (!props.baseline) return <DashboardUnavailable message={t("surface.absent")} />;
    return (
      <DashboardEditor
        {...props}
        committed={props.baseline}
        capabilities={{ canEdit: state.capabilities.canEdit && props.definition?.editable !== false, canReset: false }}
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

function DashboardSkeleton({
  className,
  label,
  toolbar,
}: {
  className?: string;
  label: string;
  toolbar?: React.ReactNode;
}): React.ReactElement {
  return (
    <SkeletonStatus
      label={label}
      className={cn("flex min-h-0 flex-1 flex-col", className)}
    >
      <div aria-hidden="true" className="flex min-h-14 flex-none items-center justify-between gap-4 border-b border-border-subtle px-4 py-3">
        <div className="min-w-0 flex-1 space-y-2">
          <Skeleton shape="text" size="lg" className="w-52 max-w-2/3" />
          <Skeleton shape="text" size="sm" className="w-72 max-w-4/5" />
        </div>
        <Skeleton className="h-btn-sm w-24 shrink-0" />
      </div>
      {toolbar}
      <div aria-hidden="true" className="grid min-h-0 flex-1 grid-cols-12 auto-rows-[56px] gap-4 overflow-hidden p-4">
        {[
          "col-span-12 row-span-3 md:col-span-5",
          "col-span-12 row-span-3 md:col-span-7",
          "col-span-12 row-span-4 md:col-span-8",
          "col-span-12 row-span-4 md:col-span-4",
        ].map((placement, index) => (
          <Card key={placement} className={cn("p-4", placement)}>
            <Skeleton shape="text" size="md" className={index % 2 === 0 ? "w-32" : "w-24"} />
            <SkeletonText lines={index < 2 ? 3 : 5} className="mt-5" />
          </Card>
        ))}
      </div>
    </SkeletonStatus>
  );
}

function ReadyDashboardSurface(
  props: DashboardSurfaceProps & {
    registry: DashboardRegistry;
    definition?: DashboardDefinition;
    baseline: DashboardSnapshot | null;
    state: Extract<DashboardLoadState, { status: "ready" }>;
    binding: DashboardStoreBinding;
  },
): React.ReactElement {
  const { state } = props;
  const visible = visibleDashboardSnapshot(props.registry, props.target, state, props.definition, props.baseline);
  const declared = visible !== state.snapshot;
  const canEdit = state.capabilities.canEdit && props.definition?.editable !== false;
  return (
    <DashboardEditor
      {...props}
      title={props.title ?? (declared ? props.definition?.title : state.name)}
      committed={visible}
      capabilities={{ canEdit, canReset: state.capabilities.canReset && canEdit }}
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
      onReset={state.capabilities.canReset && canEdit ? async () => {
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
  subtitle,
  headerDensity = "compact",
  headingLevel = 2,
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
      <SurfaceHeader
        density={headerDensity}
        headingLevel={headingLevel}
        subtitle={subtitle}
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
      <PageBody gutter="none" scroll="auto">
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
      </PageBody>
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

function DashboardGrid(props: React.ComponentProps<typeof DashboardLayout>): React.ReactElement {
  const policies = props.snapshot.widgets.flatMap((widget) =>
    !widget.isArchived && widget.visibility ? [{ id: widget.id, policy: widget.visibility }] : [],
  );
  const useVisibility = props.registry.store?.useWidgetVisibility;
  if (policies.length && useVisibility) {
    return <DashboardVisibility {...props} policies={policies} useVisibility={useVisibility} />;
  }
  return <DashboardLayout {...props} visibleIds={props.snapshot.widgets
    .filter((widget) => !widget.visibility).map(({ id }) => id)} />;
}

function DashboardVisibility({ policies, useVisibility, ...props }: React.ComponentProps<typeof DashboardLayout> & {
  policies: readonly { id: string; policy: NonNullable<WidgetSpec["visibility"]> }[];
  useVisibility: NonNullable<NonNullable<DashboardRegistry["store"]>["useWidgetVisibility"]>;
}): React.ReactElement {
  const t = useDashboardT();
  const visibility = useVisibility(policies.map(({ policy }) => policy));
  if (visibility.error) return <ErrorBanner description={visibility.error.message} />;
  if (visibility.loading) return <SkeletonStatus label={t("surface.loading")} />;
  const permitted = new Set(policies.filter((_, index) => visibility.allowed[index] === true).map(({ id }) => id));
  return <DashboardLayout {...props} visibleIds={props.snapshot.widgets
    .filter((widget) => !widget.visibility || permitted.has(widget.id)).map(({ id }) => id)} />;
}

function DashboardLayout({ snapshot, registry, definition, editing, pageScope, onWidgetsChange, visibleIds }: {
  snapshot: DashboardSnapshot;
  registry: DashboardRegistry;
  definition?: DashboardDefinition;
  editing: boolean;
  pageScope?: DashboardPageScope;
  onWidgetsChange: (widgets: readonly WidgetSpec[]) => void;
  visibleIds?: readonly string[];
}): React.ReactElement {
  const t = useDashboardT();
  const sensors = useDndKitSensors(6);
  const [containerRef, responsiveColumns] = useResponsiveColumns(snapshot.columns);
  const columns = editing ? snapshot.columns : responsiveColumns;
  const active = snapshot.widgets.filter((widget) => !widget.isArchived && (!visibleIds || visibleIds.includes(widget.id)));
  const retained = snapshot.widgets.filter((widget) => !active.includes(widget));
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
    const style = element ? getComputedStyle(element) : null;
    const contentWidth = element && style
      ? element.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight) : 0;
    const cellWidth = (contentWidth - GRID_GAP * (columns - 1)) / columns;
    const widget = projected.find((item) => item.id === String(dragged.id));
    if (!widget || cellWidth <= 0) return;
    const moved = moveDashboardRect(
      active,
      widget.id,
      { x: widget.x + Math.round(delta.x / (cellWidth + GRID_GAP)), y: widget.y + Math.round(delta.y / (ROW_HEIGHT + GRID_GAP)) },
      columns,
    );
    onWidgetsChange([...moved, ...retained]);
  };
  return (
    <div ref={containerRef} className={cn("min-w-0 p-4", editing && "min-w-[720px]")}>
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
                  ...retained,
                ])}
                onResize={(dw, dh) => onWidgetsChange([
                  ...resizeDashboardRect(active, widget.id, { w: widget.w + dw, h: widget.h + dh }, columns),
                  ...retained,
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
  if (columns < 4 || widget.data.shape === "rows" || widget.data.shape === "none" || widget.data.shape === "resourceView") return columns;
  return Math.ceil(columns / 2);
}

function responsiveWidgetHeight(widget: WidgetSpec, columns: number, canonicalColumns: number): number {
  if (columns >= canonicalColumns) return widget.h;
  if (widget.data.shape === "series") return Math.max(widget.h, 5);
  if (widget.data.shape === "rows") return Math.max(widget.h, 6);
  if (widget.data.shape === "resourceView") return Math.max(widget.h, 6);
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
  const [count, setCount] = React.useState<number | null>(null);
  const { resourceViews } = useAppRuntime();
  const sourceResource = widget.data.shape === "none" ? ""
    : widget.data.shape === "resourceView" ? resourceViews[widget.data.preset]?.resource ?? ""
    : widget.data.source.resource;
  const preset = widget.data.shape === "resourceView" ? resourceViews[widget.data.preset] : undefined;
  const sourceFilter = widget.data.shape === "none" || widget.data.shape === "resourceView" ? preset?.filter : widget.data.source.filter;
  const search = resourceViewStateToSearch(createResourceViewState({
    preset: preset?.id,
    filter: Filter.combineOptional(sourceFilter, pageScope?.resource === sourceResource ? pageScope.filter : undefined),
  }));
  const resourceRoute = useResourceRoute(sourceResource, search);
  const routeHref = useRouteHref();
  const fullViewRoute = typeof widget.options.fullViewRoute === "string" ? widget.options.fullViewRoute : undefined;
  const fullViewHref = fullViewRoute ? routeHref.maybe(fullViewRoute, undefined, search) : resourceRoute;
  const sourceMetadata = useModelMetadata(sourceResource);
  const resourceLabel = sourceMetadata?.resource.modelName
    ? titleCase(sourceMetadata.resource.modelName)
    : titleCase(sourceResource.split(".").at(-1) ?? sourceResource);
  const visibleTitle = normalizeDashboardWidgetTitle(widget.title, sourceResource, resourceLabel);
  const titleId = React.useId();
  const drag = useDraggable({ id: widget.id, disabled: !editing });
  const transform = drag.transform;
  return (
    <Card asChild>
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
        className={cn("relative flex min-h-0 flex-col overflow-hidden", drag.isDragging && "z-10 opacity-90")}
        style={{
          gridColumn: `${widget.x + 1} / span ${widget.w}`,
          gridRow: `${widget.y + 1} / span ${widget.h}`,
          ...(transform ? { transform: `translate3d(${transform.x}px, ${transform.y}px, 0)` } : {}),
        }}
      >
        <CardHeader className={cn("min-h-11 shrink-0 flex-row items-center gap-2 py-2",
          widget.data.shape !== "value" && "border-b border-border-subtle",
          !editing && widget.data.shape !== "none" && widget.data.shape !== "resourceView" && "pr-12")}>
          <div id={titleId} className={editing ? "sr-only" : "min-w-0 flex-1"}>
            <SectionHeading presentation="title"
              label={!editing && fullViewHref ? <NavLink href={fullViewHref} className="hover:underline">{visibleTitle}</NavLink> : visibleTitle}
              count={count == null ? undefined : `· ${count}`}
              hint={typeof widget.options.hint === "string" ? `· ${widget.options.hint}` : undefined}
              audience={typeof widget.options.audience === "string" ? widget.options.audience : undefined}
            />
          </div>
          {!editing && fullViewHref ? <NavLink href={fullViewHref} aria-label={t("surface.fullView", { title: visibleTitle })} affordance="outward" className="shrink-0 rounded-4 p-1 text-sm text-fg-muted hover:text-fg focus-visible:focus-ring" /> : null}
          {editing ? <Button type="button" variant="ghost" size="iconSm" className="cursor-grab" aria-label={t("surface.move", { title: visibleTitle })} {...drag.attributes} {...drag.listeners}><Glyph name="grip-vertical" fallbackName="more-vertical" /></Button> : null}
          {editing ? (
            <Input
              size="sm"
              value={visibleTitle}
              onChange={(event) => onUpdate({ title: event.target.value })}
              aria-label={t("surface.widgetTitle")}
              className="min-w-0 flex-1"
            />
          ) : null}
          {editing ? (
            <>
              <Button type="button" variant="ghost" size="iconSm" aria-label={t("surface.moveLeft")} onClick={() => onMove(-1, 0)}><Glyph name="arrow-left" /></Button>
              <Button type="button" variant="ghost" size="iconSm" aria-label={t("surface.moveRight")} onClick={() => onMove(1, 0)}><Glyph name="arrow-right" /></Button>
              <Button type="button" variant="ghost" size="iconSm" aria-label={t("surface.wider")} onClick={() => onResize(1, 0)}><Glyph name="maximize-2" /></Button>
              <Button type="button" variant="ghost" size="iconSm" aria-label={t("surface.removeWidget")} onClick={onArchive}><Glyph name="archive" fallbackName="x" /></Button>
            </>
          ) : null}
        </CardHeader>
        <ResolvedWidget widget={widget} registry={registry} definition={definition} editing={editing} pageScope={pageScope} titleId={titleId} onCountChange={setCount} />
      </article>
    </Card>
  );
}

function ResolvedWidget({ widget, registry, definition, editing, pageScope, titleId, onCountChange }: {
  widget: WidgetSpec;
  registry: DashboardRegistry;
  definition?: DashboardDefinition;
  editing: boolean;
  pageScope?: DashboardPageScope;
  titleId: string;
  onCountChange: (count: number | null) => void;
}): React.ReactElement {
  const t = useDashboardT();
  const kind = registry.widgetKinds[widget.kind];
  if (!kind || kind.version !== widget.kindVersion || kind.shape !== widget.data.shape) {
    return <ErrorBanner className="m-3" description={t("surface.widgetKindUnavailable", { kind: widget.kind, version: widget.kindVersion })} />;
  }
  return <WidgetDataBody widget={widget} kind={kind} definition={definition} editing={editing} pageScope={pageScope} titleId={titleId} onCountChange={onCountChange} />;
}

function normalizeDashboardWidgetTitle(title: string, resource: string, resourceLabel: string): string {
  if (!resource) return title;
  if (title === resource) return resourceLabel;
  if (title.startsWith(`${resource} `)) return `${resourceLabel}${title.slice(resource.length)}`;
  return title;
}

function WidgetDataBody({ widget, kind, definition, editing, pageScope, titleId, onCountChange }: {
  widget: WidgetSpec;
  kind: DashboardWidgetKind;
  definition?: DashboardDefinition;
  editing: boolean;
  pageScope?: DashboardPageScope;
  titleId: string;
  onCountChange: (count: number | null) => void;
}): React.ReactElement {
  const t = useDashboardT();
  const data = useDashboardWidgetData(widget, pageScope);
  React.useEffect(() => {
    if (widget.data.shape !== "resourceView" && widget.data.shape !== "none") {
      onCountChange(data.count ?? null);
    }
  }, [data.count, data.value, onCountChange, widget.data.shape, widget.kind]);
  const Component = kind.Component;
  const Authored = definition?.authored?.[widget.id];
  const HostedView = widget.data.shape === "resourceView" ? definition?.views?.[widget.data.preset] : undefined;
  return (
    <CardContent className="flex min-h-0 flex-1 flex-col overflow-hidden">
      <div className={cn("min-h-0 flex-1", kind.shape === "rows" || kind.shape === "resourceView" ? "overflow-auto" : "overflow-hidden")}>
        <Component spec={widget} data={data} titleId={titleId} pageScope={pageScope}
          authored={Authored ? <Authored /> : undefined} hostedView={HostedView} onCountChange={onCountChange} />
      </div>
      {!editing && widget.data.shape !== "none" && widget.data.shape !== "resourceView" ? (
        <ActionMenu label={null} aria-label={t("surface.widgetMenu", { title: widget.title })} glyph="more-horizontal" size="iconSm" align="end" className="absolute right-3 top-2">
          <ActionTrigger glyph="refresh" onClick={data.refetch}>{t("surface.refresh")}</ActionTrigger>
        </ActionMenu>
      ) : null}
    </CardContent>
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

/**
 * @kind surface
 */
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
