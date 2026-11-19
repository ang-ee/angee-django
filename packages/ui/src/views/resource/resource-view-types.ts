import type { Row, QueryFilter, ResourceFilter, ResourceOrder } from "@angee/metadata";
import type { ListSearchDeclaration } from "./search/shortcuts";
import type {
  ReactNode } from "react";
import type {
  ResourceTypeName,
} from "@angee/metadata";
import type {
  ResourceViewDefaultGroups,
  ResourceViewGroup,
  ResourceViewKind,
} from "./resource-view-model";
import type { ButtonVariant } from "../../ui/button";
import { cardFrameVariants, type CardFrame } from "../../ui/card";
import { recipeAxes } from "../../lib/variants";
import type { ResourceCardContent } from "../../runtime/containers";

import type {
  FilterClauseField,
  ResourceToolbarFilterOption,
  ResourceToolbarGroupOption,
  ResourceToolbarChrome,
  ResourceToolbarProps,
} from "../../toolbars";
import type {
  ListViewNavigationScope,
  ResourceListSnapshot,
} from "./resource-view-surface";
import type { ColumnDescriptor, FacetDescriptor } from "../page";
import type { Occurrence } from "../calendar/CalendarView";
import type { AnyCalendarWindowSource } from "../calendar/use-calendar-window";
import type { GanttRowLayout } from "../gantt/gantt-types";
import type { GanttLaneDetails } from "../gantt/gantt-lane";
import type { DndPayload } from "../../lib/dnd";
import type { RowActionDeclaration } from "./RowActions";
import type { CrudFilter, CrudSort } from "@refinedev/core";
import type { CollectionSource } from "./collection-source";
import type { AggregateBucket } from "@angee/refine";

export type { ListSearchDeclaration, SearchShortcut } from "./search/shortcuts";
export { validateSearchShortcut } from "./search/shortcuts";

export interface GroupLabelContext {
  bucket: AggregateBucket;
  label: string;
  depth: number;
}

/**
 * The calendar kind's data declaration. The windowed-collection surface fetches
 * each source per visible window, merges the occurrences, and renders the grid;
 * `onReschedule` and `onSelectRange` are the drag and quick-create seams.
 */
export interface CalendarViewSpec {
  /** Occurrence sources fetched per visible window and merged onto the grid. */
  sources: readonly AnyCalendarWindowSource[];
  /** Persist an editable occurrence's drag/resize; reject to revert the grid. */
  onReschedule?: (
    occurrence: Occurrence,
    start: Date,
    end: Date | null,
  ) => void | Promise<unknown>;
  /** Quick-create seam: a range select (whole days when `allDay`) seeds and opens the create form. */
  onSelectRange?: (start: Date, end: Date, allDay: boolean) => void;
}

export interface BoardLaneSource {
  /** Resource field that owns the board lane id and receives drag writes. */
  field: string;
  /** Optional display-field override; defaults to the related model representation. */
  labelField?: string;
  /** Server-side filters narrowing the related rows that become board lanes. */
  filters?: readonly CrudFilter[];
  /** Explicit server order for the related rows that become board lanes. */
  sorters?: readonly CrudSort[];
  /** Writable Float field that owns manual order inside this lane context.
   * A ResourceList create form declares it (usually `createOnly`) to submit the
   * lane quick-create seed through FormView's existing default-values contract. */
  rankField?: string;
  /** Write-input extension that accepts loaded preceding/following public IDs.
   * The server allocates the canonical rank in its full ordering context. */
  rankInputField?: string;
  /** Boolean field on the related lane resource that declares its default fold. */
  foldField?: string;
}

/** Read-only schedules on rows supplied by the list's related lane source. */
export interface GanttViewSpec extends GanttRowLayout {
  start: string;
  end: string;
  /** Bars from another resource; the list resource itself supplies lanes, filters, presets, and selection. */
  linked?: { resource: string; lane: string; filter?: ResourceFilter<ResourceTypeName> };
  /** Defaults to the resource's record representation. */
  label?: string;
  /** A status field resolved through the runtime's status-tone vocabulary. */
  tone?: string;
  /** Field on the lane resource holding the current bar's identity (scalar id or to-one relation). */
  current?: string;
  /** Read-only point events from a second resource, scoped to the same lane page. */
  markers?: {
    resource: string;
    /** Relation group axis on the marker resource, targeting the lane resource. */
    lane: string;
    date: string;
    /** Defaults to the marker resource's record representation. */
    label?: string;
    tone?: string;
    /** Marker-specific scope; the bars' filters and search never apply to this resource. */
    filter?: ResourceFilter<ResourceTypeName>;
  };
  /** Declared lane content, selected from the lane resource even for empty lanes. */
  lane?: {
    fields?: readonly string[];
    content: (row: Row) => GanttLaneDetails;
  };
}

/** Loaded neighbors around a requested insertion, in visual order. */
export interface BoardOrderIntent {
  preceding: readonly string[];
  following: readonly string[];
}

/** Exact numeric rank, or neighbor intent for a server-owned ordering scope. */
export type BoardOrder = number | BoardOrderIntent;

/** One card's optimistic board placement while its server write settles. */
export interface BoardCardPlacement {
  laneId: string;
  rank?: number;
}

/** Declared default board card: one title and up to four readable detail fields. */
export interface BoardCardSpec {
  title: string;
  fields?: readonly string[];
}

/** Declared default gallery card: an image, a title and a secondary line, each a readable field. */
export interface GalleryCardSpec<TRow extends Row = Row> {
  image?: keyof TRow & string;
  title?: keyof TRow & string;
  subtitle?: keyof TRow & string;
}

export interface CardActionContext {
  /** Re-pull the collection backing the board/list surface. */
  refresh: () => void;
}

/** One group of the list's grouping as its header shows it. */
export interface SelectedGroupLevel {
  /** The axis as the resource declares it: `display_name`. */
  field: string;
  /**
   * The group's identity as text (`String` of it): a text column's stored value, a
   * relation's id, a date bucket's start, a number's or a boolean's text (`"3"`,
   * `"true"`), a JSON value's serialization; null when the rows have no value (the
   * blank text group is "").
   */
  key: string | null;
  /** What the group header shows, the list's own wording for an empty group included. */
  label: string;
}

/** The selected rows of one innermost group (`bulkActions` → `selectedGroups`). */
export interface SelectedGroup extends SelectedGroupLevel {
  /** The enclosing groups, outermost first; [] at the root. */
  parents: readonly SelectedGroupLevel[];
  /** The group's selected rows in selection-map (object key) order: selection order,
   * except that integer-like ids come first, ascending. */
  ids: readonly string[];
}

/**
 * The groups the server-grouped surface shows a record under: one level per grouping
 * level, outermost first, with the group's ordinal among its level's groups, its
 * header's bucket key and the key of the level page that lists it
 * (`GroupedRenderShown.pages`). Internal to the list; `SelectedGroup` is the public split.
 */
export type SelectionGroupPath = readonly {
  group: ResourceViewGroup;
  key: string | null;
  label: string;
  position: number;
  bucketKey: string;
  page: string;
}[];

/** Where the server-grouped surface shows a record: its groups and the key of its group's row page. Internal. */
export interface SelectionPlace {
  path: SelectionGroupPath;
  page: string;
}

/**
 * What a card renderer knows about the card it draws, discriminated by the
 * surface: narrow on `kind` before reading a board-only fact.
 * One renderer serves both surfaces.
 */
export type CardRenderContext =
  | {
      kind: "gallery";
      /** The collection has this card selected. */
      selected: boolean;
    }
  | {
      kind: "board";
      selected: boolean;
      /** The card is being dragged. */
      dragging: boolean;
      /** The lane's group value (`RowGroup.identity`): the related record's id or the
       * grouped value, `null` for the records with no value; absent on an ungrouped board. */
      laneId?: string | null;
    };

/**
 * Draw one card's body; the surface keeps the frame, link, selection and actions.
 * The surface calls it as a plain function while it renders, so it must not call
 * hooks: return elements, and put hooks in a component it returns
 * (`(row, context) => <EventCard row={row} context={context} />`). The body is
 * inside the card's link or button, so it holds no interactive element.
 */
export type CardRenderer<TRow extends Row = Row> = (row: TRow, context: CardRenderContext) => ReactNode;

/**
 * Check a registered card's content (`<app.Model>#card`) as app creation does:
 * `render` is a function and `frame`, when set, a card frame.
 * @kind null
 * @reason Schema infrastructure: validates serialized runtime contracts.
 */
export function validateResourceCard(content: unknown, id: string): asserts content is ResourceCardContent {
  const card = typeof content === "object" && content !== null ? content as Record<string, unknown> : null;
  if (!card || typeof card.render !== "function") throw new Error(`Card "${id}" needs a render function.`);
  const frames: readonly string[] = recipeAxes(cardFrameVariants).frame;
  if (card.frame !== undefined && !frames.includes(String(card.frame))) {
    throw new Error(`Card "${id}" names frame "${String(card.frame)}"; a card frame is ${frames.map((frame) => `"${frame}"`).join(" or ")}.`);
  }
}

export interface ListEmptyAction {
  label: ReactNode;
  href?: string;
  onClick?: () => void;
  icon?: ReactNode | string;
  variant?: ButtonVariant;
}

export interface ListEmptyState {
  title: ReactNode;
  description?: ReactNode;
  icon?: ReactNode | string;
  action?: ListEmptyAction;
  actions?: ReactNode;
}

export type ListEmptyContent = ReactNode | ListEmptyState;

/** Vertical-scroll ownership for a collection surface. */
export type ResourceCollectionPresentation = "page" | "workspace" | "embedded";
export type ResourceTableLayout = "auto" | "fixed";
export type ResourceTableHeaderVisibility = "visible" | "visually-hidden";

/**
 * Declared list chrome. Each declared key overrides the presentation's default:
 * a page or workspace list shows every control; an embedded list is compact
 * (see {@link listChromeState}).
 */
export interface ListChrome extends ResourceToolbarChrome {
  columnChooser?: boolean;
  /** Copy surrounding the live collection count in the shared heading line;
   * `false` leaves an embedded list's heading to its host. */
  heading?: { label: ReactNode; hint?: ReactNode; audience?: ReactNode } | false;
}

/** The chrome one list render shows, resolved from its presentation, declaration and collection. */
export interface ListChromeState extends Required<Omit<ListChrome, "heading">> {
  /** An embedded list's heading row replaces the control band. */
  compact: boolean;
}

/**
 * Resolve a list's chrome. An embedded list is compact: its heading row carries
 * the label, live count, create and toolbar actions; the view switcher, column
 * chooser and row selection stay hidden; search and pager appear only once the
 * collection outgrows one page, leaves its first page or carries a query beyond
 * its default. Declared `chrome` keys override every default.
 */
export function listChromeState(
  presentation: ResourceCollectionPresentation | undefined,
  chrome: ListChrome | undefined,
  collection: { total: number | undefined; page: number; pageSize: number; hasNext?: boolean; queryDirty: boolean },
): ListChromeState {
  const compact = presentation === "embedded";
  const paged = !compact || (collection.total ?? 0) > collection.pageSize || collection.page > 1
    || Boolean(collection.hasNext);
  return {
    compact,
    viewSwitcher: chrome?.viewSwitcher ?? !compact,
    columnChooser: chrome?.columnChooser ?? !compact,
    search: chrome?.search ?? (paged || collection.queryDirty),
    pager: chrome?.pager ?? paged,
  };
}

export interface ListViewProps<TRow extends Row = Row> {
  /** Model label rendered by this list, e.g. `"notes.Note"`. */
  resource: string;
  /**
   * Canonical model labels whose changes refresh this native list when
   * related_records names any record of its model, including rows off the page.
   * Authored collection sources declare their interests on their queries.
   */
  relatedModels?: readonly string[];
  /** Show or hide list chrome while preserving the resource view's query. */
  chrome?: ListChrome;
  /** Page/workspace surfaces fill their owner with full chrome; embedded
   * surfaces grow in flow with compact chrome. */
  presentation?: ResourceCollectionPresentation;
  /** CSS table sizing strategy. Fixed layout lets rich single-column rows truncate to their pane. */
  tableLayout?: ResourceTableLayout;
  /** Keep column headers visible or accessible-only. Defaults to visible. */
  headerVisibility?: ResourceTableHeaderVisibility;
  /** Enable row selection and the bulk-selection column. Outside embedded lists,
   * defaults on for lifecycle actions, page bulkActions or a grantable model.
   * A source list counts only page bulkActions; an explicit value always wins. Selection survives pagination, page-size and view
   * changes that keep the same grouping. setView can replace groupStack with the
   * new view's defaults, clearing selection if the grouping changes. Filter, sort,
   * group, preset query/fixed-filter, base-filter, resource and Router-history
   * collection changes clear it. Only one scope holds selection: entering another
   * inherited scope or selecting at the owner clears the previous scope. */
  selectable?: boolean;
  /** Authored server projection using the same native collection surface. */
  source?: CollectionSource<TRow>;
  /** Allowed render kinds; defaults to the resource's available kinds. */
  availableViews?: readonly ResourceViewKind[];
  /** Shipped view ids declared for this collection route. */
  presetIds?: readonly string[];
  /** Default box text field; null removes only that default, preserving per-field text shortcuts. */
  textFilterField?: string | null;
  /** Limit nested grouping where the source supports a single axis. */
  maxGroupDepth?: number;
  /** Wrap native controls inside a narrow embedded collection pane. */
  toolbarWrap?: boolean;
  /** A navigable group title; the native chevron independently expands its rows. */
  renderGroupLabel?: (group: GroupLabelContext) => ReactNode;
  /** Columns rendered by the list. */
  columns: readonly ColumnDescriptor<TRow>[];
  /** Pane pointers over the same paged/grouped collection. The renderer owns
   * each row's link or button; the list owns PaneRows, labels and group pagers. */
  renderPaneRow?: (row: TRow) => ReactNode;
  /** Extra resource fields selected in addition to the declared columns. */
  fields?: readonly string[];
  /** Base resource filter applied before user-owned view filters. */
  baseFilter?: ResourceFilter<ResourceTypeName>;
  /** Named predicates available in search and toggle shortcuts. */
  filterOptions?: readonly ResourceToolbarFilterOption[];
  /** Combined box and optional shortcuts over the shared search model. */
  search?: ListSearchDeclaration;
  /** Explicit relation facet catalogs and group axes. */
  facets?: readonly FacetDescriptor[];
  /** Presentation overrides for typed clauses; the query supplies all supported fields. */
  customFilterFields?: readonly FilterClauseField[];
  /** Curated group axes; the complete search catalog retains every supported axis. */
  groupOptions?: readonly ResourceToolbarGroupOption[];
  /** Default resource order when the URL-owned data view has no sort. */
  order?: ResourceOrder<ResourceTypeName>;
  /** Initial page size for the URL-owned data view. */
  pageSize?: number;
  /** Initial collection view for the resource list. */
  defaultView?: ResourceViewKind;
  /** Calendar data + interaction seams. When declared, the Calendar kind is offered
   * in the switcher and rendered as a windowed-collection surface (no `useList`). */
  calendar?: CalendarViewSpec;
  /** Date-scaled bars on related rows or a linked resource. */
  gantt?: GanttViewSpec;
  /** Declared board lanes for a relation group field; empty lanes render too. */
  laneSource?: BoardLaneSource;
  /** Card content without replacing the board's shared frame and actions. */
  boardCard?: BoardCardSpec;
  /** The gallery kind's default card fields (image, title, subtitle). Without it
   * the gallery draws the board's column card; `renderCard` overrides both. */
  gallery?: GalleryCardSpec<TRow>;
  /** Default grouping stack; a single group declares one level. */
  defaultGroup?: ResourceViewGroup | readonly ResourceViewGroup[] | null;
  /** Per-view group defaults seeded by the resource list. */
  defaultGroups?: ResourceViewDefaultGroups;
  /** Initial expansion policy for server-grouped list roots. Defaults to all. */
  defaultExpandedGroups?: "all" | "none";
  /** Called when the list's create command is invoked. */
  onCreate?: () => void;
  /** Quick-create seam for a board lane; the resource page owns opening create. */
  onCreateInLane?: (laneId: string | null, rank?: number) => void;
  /** Label for the list's create command. */
  createLabel?: ReactNode;
  /** Called when a row is activated. */
  onRowClick?: (row: TRow) => void;
  /** Called whenever the loaded list state changes. */
  onListStateChange?: (state: ResourceListSnapshot<TRow>) => void;
  /** Optional href for a row, used when rows should render as links. */
  rowHref?: (row: TRow, scope?: ListViewNavigationScope) => string;
  /** Authored and page-owned verbs rendered in the shared trailing action column. */
  rowActions?: readonly RowActionDeclaration<TRow>[];
  /** Native cross-pane drag payload for each record row, including grouped rows. */
  draggableRow?: (row: TRow) => DndPayload | null;
  /** Controls rendered in the toolbar's leading slot, beside the filter — e.g. a
   * "Connect" button for a list whose rows come from a connect flow. */
  toolbarActions?: ReactNode;
  /** Present resource controls in the shared pane band; callers supply only its lens and scope. */
  pane?: ResourceToolbarProps["pane"];
  /** Domain actions, called only when selectable and something is selected.
   * The third argument describes the surface's query: an empty filter ({}) means
   * the whole collection; the server does not check selection membership against
   * this descriptive filter. It is the list's filter: under a repeated grouping it
   * still describes every filtered record, not only the repeated groups' records.
   * selectedRows contains snapshots captured when
   * selected, in selection-map (object key) order, surviving page unloads and
   * surface remounts. Ids selected without a row are omitted; retained snapshots
   * are not refreshed until deselection/reselection. Actions needing fresh rows
   * must validate them, for example by revision. Surfaces sharing a selection
   * scope share its snapshots: a row selected through a sibling surface carries
   * that surface's fields, so actions must check the fields they need.
   * selectedGroups is the selection split by the grouping the list renders, in
   * three states; never an empty array, since without a selection the bulk actions
   * do not render. Undefined: the list is not grouped (a gallery never groups).
   * null: it is grouped but at least one selected id cannot be placed, so no split
   * is offered, never a partial one: the id was selected while the list was flat or
   * grouped otherwise, or by a select-all that holds ids without rows; it shows now
   * under another group than the one it was selected in, or under more than one; a
   * page that listed it, or listed one of its groups, shows again without it or is
   * still loading; or the surface never places rows (a client-grouped list, a lane
   * board, whose every selected card yields null, and `RowsListView`; pane lists do
   * not select). Otherwise one entry per innermost group holding a selected row,
   * every selected id in exactly one entry: a row the list shows in the group it
   * shows in, any other row in the group it was selected in (a collapsed group,
   * another page of its group or another page of groups cannot tell otherwise).
   * Entries follow the groups' display order, each group at its current position
   * where a loaded page lists it, else at the position captured with the
   * selection; ties keep selection-map order. It is a hint, never proof: like
   * filter the server does not check it, and a verb acting on it validates the
   * records' current state there.
   * Rendered content precedes the generic metadata-driven Archive, Trash/Restore
   * and Delete actions. A source list receives only these page-owned actions. */
  bulkActions?: (
    selectedIds: ReadonlySet<string>,
    clear: () => void,
    collection: {
      filter: QueryFilter;
      selectedRows: readonly TRow[];
      selectedGroups: readonly SelectedGroup[] | null | undefined;
    },
  ) => ReactNode;
  /** Optional action content rendered in each board card footer. */
  cardActions?: (row: TRow, context: CardActionContext) => ReactNode;
  /** Card body override for the board and the gallery — a rich card (art,
   * description, chips, badges) instead of the default title + key/value rows.
   * The context names the surface, lane and selection; the lane grouping, frame
   * link/click, selection and the `cardActions` footer are unchanged. */
  renderCard?: CardRenderer<TRow>;
  /** How a card's content meets its frame on both surfaces: `inset` pads it,
   * `flush` lets an art header reach the card's edges with the controls overlaid.
   * Defaults to each surface's own (board `inset`, gallery `flush`). */
  cardFrame?: CardFrame;
  /** Empty-state content shown when the list has no rows. */
  emptyContent?: ListEmptyContent;
  /** Class name applied to the collection renderer root. */
  className?: string;
  /** Override collection-state ownership. Embedded collections default to local
   * state; page/workspace collections inherit an ambient view or own route state. */
  scope?: "inherit" | "local";
}
