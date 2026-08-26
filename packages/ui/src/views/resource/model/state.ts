import { clampPageSize, stableSerialize } from "@angee/refine";
import { dedupeBy } from "../../../lib/dedupe";
import { DEFAULT_CALENDAR_VIEW_MODE, DEFAULT_RESOURCE_VIEW_PAGE_SIZE, defaultResourceViewPageSize } from "./capabilities";
import type { CalendarViewMode, ResourceViewKind } from "./capabilities";
import { nextResourceViewFavoriteId } from "./favorites";
import type { ResourceViewFavorite } from "./favorites";
import { Filter } from "./filter";
import type { ResourceViewAction, ResourceViewFilter, ResourceViewGroup, ResourceViewInitialState, ResourceViewResourceOrder, ResourceViewSort } from "./filter";
import { isClearedSearchValue, parseSearchAnchor, parseSearchFilter, parseSearchGroup, parseSearchGroupStack, parseSearchInteger, parseSearchMode, parseSearchSort, parseSearchView, serializeResourceViewGroup, serializeResourceViewGroupStack, serializeResourceViewSort, todayCalendarAnchor } from "./search";
import type { ResourceViewSearch } from "./search";
export class ResourceViewState {
  readonly page: number;
  readonly pageSize: number;
  readonly sort: ResourceViewSort | null;
  readonly filter: ResourceViewFilter;
  readonly group: ResourceViewGroup | null;
  readonly groupStack: readonly ResourceViewGroup[];
  readonly selectedIds: ReadonlySet<string>;
  readonly view: ResourceViewKind;
  readonly mode: CalendarViewMode;
  readonly anchor: string;

  constructor(initial: ResourceViewInitialState = {}) {
    const groupStack = ResourceViewState.normaliseGroupStack(
      initial.groupStack ?? (initial.group ? [initial.group] : []),
    );
    this.page = ResourceViewState.normalisePage(initial.page);
    this.pageSize = clampPageSize(
      initial.pageSize ?? DEFAULT_RESOURCE_VIEW_PAGE_SIZE,
    );
    this.sort = initial.sort ? ResourceViewState.normaliseSort(initial.sort) : null;
    this.filter = ResourceViewState.normaliseFilter(initial.filter);
    this.group = groupStack[0] ?? null;
    this.groupStack = groupStack;
    this.selectedIds = new Set(initial.selectedIds ?? []);
    this.view = initial.view ?? "list";
    this.mode = initial.mode ?? DEFAULT_CALENDAR_VIEW_MODE;
    this.anchor = initial.anchor ?? todayCalendarAnchor();
  }

  static create(initial: ResourceViewInitialState = {}): ResourceViewState {
    return new ResourceViewState(initial);
  }

  static fromSearch(
    search: ResourceViewSearch | Record<string, unknown>,
    initial: ResourceViewInitialState = {},
  ): ResourceViewState {
    const base = ResourceViewState.create(initial);
    const sortCleared = isClearedSearchValue(search.sort);
    const filterCleared = isClearedSearchValue(search.filter);
    const groupCleared = isClearedSearchValue(search.group);
    const thenCleared = isClearedSearchValue(search.then);
    const page = parseSearchInteger(search.page);
    const pageSize = parseSearchInteger(search.pageSize);
    const sort = parseSearchSort(search.sort);
    const filter = parseSearchFilter(search.filter);
    const group = parseSearchGroup(search.group);
    const then = parseSearchGroupStack(search.then);
    const view = parseSearchView(search.view);
    const mode = parseSearchMode(search.mode);
    const anchor = parseSearchAnchor(search.anchor);
    return ResourceViewState.create({
      ...base.toInitialState(),
      page: page ?? base.page,
      pageSize: pageSize ?? base.pageSize,
      sort: sortCleared ? null : (sort ?? base.sort),
      filter: filterCleared ? {} : (filter ?? base.filter),
      group: groupCleared ? null : (group ?? base.group),
      groupStack:
        groupCleared
          ? []
          : group || then || thenCleared
          ? [
              ...(group ? [group] : []),
              ...(thenCleared ? [] : (then ?? [])),
            ]
          : base.groupStack,
      view: view ?? base.view,
      mode: mode ?? base.mode,
      anchor: anchor ?? base.anchor,
    });
  }

  reduce(action: ResourceViewAction): ResourceViewState {
    switch (action.type) {
      case "setPage":
        return this.with({ page: ResourceViewState.normalisePage(action.page) });
      case "setPageSize":
        return this.resetQueryScope({
          pageSize: clampPageSize(action.pageSize),
        });
      case "setSort":
        return this.resetQueryScope({
          sort: action.sort ? ResourceViewState.normaliseSort(action.sort) : null,
        });
      case "setFilter":
        return this.resetQueryScope({
          filter: ResourceViewState.normaliseFilter(action.filter),
        });
      case "setGroup":
        return this.resetQueryScope({
          group: action.group ? ResourceViewState.normaliseGroup(action.group) : null,
          groupStack: action.group
            ? [ResourceViewState.normaliseGroup(action.group)]
            : [],
        });
      case "setGroupStack": {
        const groupStack = ResourceViewState.normaliseGroupStack(action.groupStack);
        return this.resetQueryScope({
          group: groupStack[0] ?? null,
          groupStack,
        });
      }
      case "setSelectedIds":
        return this.with({ selectedIds: new Set(action.selectedIds) });
      case "toggleSelectedId":
        return this.with({
          selectedIds: ResourceViewState.toggledSelectedIds(
            this.selectedIds,
            action,
          ),
        });
      case "clearSelectedIds":
        return this.with({ selectedIds: new Set() });
      case "setView":
        return this.with({ view: action.view });
      case "setMode":
        return this.with({ mode: action.mode });
      case "setAnchor":
        return this.with({ anchor: action.anchor });
      case "applyFavorite":
        return this.resetQueryScope({
          pageSize: action.favorite.pageSize,
          sort: action.favorite.sort ?? null,
          filter: action.favorite.filter ?? {},
          groupStack: action.favorite.groupStack ?? [],
          view: action.favorite.view ?? "list",
        });
    }
  }

  toSearch(initial: ResourceViewInitialState = {}): ResourceViewSearch {
    const search: ResourceViewSearch = {};
    const base = ResourceViewState.create(initial);
    const defaultPageSize = defaultResourceViewPageSize(initial);
    const defaultView = initial.view ?? "list";
    if (this.page !== 1) search.page = this.page;
    if (this.pageSize !== defaultPageSize) {
      search.pageSize = this.pageSize;
    }
    const sortValue = this.sort ? serializeResourceViewSort(this.sort) : "";
    const baseSortValue = base.sort ? serializeResourceViewSort(base.sort) : "";
    if (this.sort) {
      if (sortValue !== baseSortValue) search.sort = sortValue;
    } else if (base.sort) {
      search.sort = "";
    }
    const filterValue = stableSerialize(this.filter);
    const baseFilterValue = stableSerialize(base.filter);
    if (this.hasFilter()) {
      if (filterValue !== baseFilterValue) search.filter = JSON.stringify(this.filter);
    } else if (Filter.from(base.filter).hasEntries()) {
      search.filter = "";
    }
    const groupStackValue = serializeResourceViewGroupStack(this.groupStack);
    const baseGroupStackValue = serializeResourceViewGroupStack(base.groupStack);
    if (this.groupStack.length > 0) {
      if (groupStackValue !== baseGroupStackValue) {
        search.group = serializeResourceViewGroup(this.groupStack[0]!);
        if (this.groupStack.length > 1) {
          search.then = serializeResourceViewGroupStack(this.groupStack.slice(1));
        }
      }
    } else if (base.groupStack.length > 0) {
      search.group = "";
      if (base.groupStack.length > 1) search.then = "";
    }
    if (this.view !== defaultView) search.view = this.view;
    // mode/anchor are calendar facts: they ride the URL only under the calendar
    // kind, so a list/board deep-link never carries them.
    if (this.view === "calendar") {
      if (this.mode !== DEFAULT_CALENDAR_VIEW_MODE) search.mode = this.mode;
      if (this.anchor !== todayCalendarAnchor()) search.anchor = this.anchor;
    }
    return search;
  }

  hasFilter(): boolean {
    return Filter.from(this.filter).hasEntries();
  }

  resourceOrder(): ResourceViewResourceOrder | undefined {
    if (!this.sort) return undefined;
    return { [this.sort.field]: this.sort.dir === "asc" ? "ASC" : "DESC" };
  }

  withSelectedIds(selectedIds: Iterable<string>): ResourceViewState {
    // Selection is the hot path (toggled on every row click). Clone by structural
    // sharing so the already-normalised sort/filter/group/groupStack KEEP their
    // references — routing through `with()`/the constructor re-normalises them into
    // new objects on a pure selection change, churning every downstream memo (and
    // every memoised row) that derives from them.
    return Object.assign(
      Object.create(ResourceViewState.prototype) as ResourceViewState,
      this,
      { selectedIds: new Set(selectedIds) },
    );
  }

  toFavorite(
    label: string,
    existingFavorites: readonly ResourceViewFavorite[] = [],
  ): ResourceViewFavorite {
    return {
      id: nextResourceViewFavoriteId(label, existingFavorites),
      label,
      pageSize: this.pageSize,
      ...(this.sort ? { sort: this.sort } : {}),
      ...(this.hasFilter() ? { filter: this.filter } : {}),
      ...(this.groupStack.length > 0 ? { groupStack: this.groupStack } : {}),
      ...(this.view !== "list" ? { view: this.view } : {}),
    };
  }

  static normaliseGroupStack(
    groups: readonly ResourceViewGroup[],
  ): readonly ResourceViewGroup[] {
    return dedupeBy(groups.map((group) => ResourceViewState.normaliseGroup(group)), serializeResourceViewGroup);
  }

  private with(initial: ResourceViewInitialState): ResourceViewState {
    return ResourceViewState.create({
      ...this.toInitialState(),
      ...initial,
    });
  }

  private resetQueryScope(initial: ResourceViewInitialState): ResourceViewState {
    return ResourceViewState.create({
      ...this.toInitialState(),
      ...initial,
      page: 1,
      selectedIds: [],
    });
  }

  private toInitialState(): ResourceViewInitialState {
    return {
      page: this.page,
      pageSize: this.pageSize,
      sort: this.sort,
      filter: this.filter,
      group: this.group,
      groupStack: this.groupStack,
      selectedIds: this.selectedIds,
      view: this.view,
      mode: this.mode,
      anchor: this.anchor,
    };
  }

  private static toggledSelectedIds(
    selectedIds: ReadonlySet<string>,
    action: Extract<ResourceViewAction, { type: "toggleSelectedId" }>,
  ): ReadonlySet<string> {
    const next = new Set(selectedIds);
    const shouldSelect = action.selected ?? !next.has(action.id);
    if (shouldSelect) next.add(action.id);
    else next.delete(action.id);
    return next;
  }

  private static normalisePage(page: number | undefined): number {
    if (page === undefined || !Number.isFinite(page)) return 1;
    return Math.max(1, Math.floor(page));
  }

  private static normaliseSort(sort: ResourceViewSort): ResourceViewSort {
    return {
      field: sort.field,
      dir: sort.dir === "desc" ? "desc" : "asc",
    };
  }

  private static normaliseGroup(group: ResourceViewGroup): ResourceViewGroup {
    return {
      field: group.field,
      ...(group.aggregateField ? { aggregateField: group.aggregateField } : {}),
      ...(group.aggregateKey ? { aggregateKey: group.aggregateKey } : {}),
      ...(group.granularity ? { granularity: group.granularity } : {}),
    };
  }

  private static normaliseFilter(
    filter: ResourceViewFilter | undefined,
  ): ResourceViewFilter {
    return Filter.from(filter).value;
  }
}
