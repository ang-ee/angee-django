import * as React from "react";
import {
  type ResourceTypeName,
  type Row,
  type UseResourceListOptions,
} from "@angee/sdk";

import {
  DataToolbar,
  type DataToolbarFilterOption,
  type DataToolbarGroupOption,
} from "../toolbars";
import { Button } from "../ui/button";
import type { PagerState } from "../ui/pager";
import { Spinner } from "../ui/spinner";
import { BoardView } from "./board-view";
import {
  DataViewProvider,
  useDataView,
  useDataViewMaybe,
  type DataViewContextValue,
} from "./data-view-context";
import {
  dataViewGroupsEqual,
  type DataViewFilter,
  type DataViewGroup,
} from "./data-view-model";
import {
  useDataViewSurface,
  type ListViewState,
} from "./data-view-surface";
import {
  GroupedListBody,
  groupPagerStatesEqual,
  type GroupPagerState,
} from "./grouped-list";
import {
  FlatListBody,
  dataViewGroupToAggregateDimension,
  groupFieldLabel,
  looksLikeDateField,
  readPath,
  statusLabel,
} from "./list-internals";
import type { ColumnDescriptor } from "./page";

export type { ListViewState } from "./data-view-surface";
export type {
  ColumnAlign,
  ListColumn,
} from "./list-internals";

export interface ListViewProps<TRow extends Row = Row> {
  model: string;
  columns: readonly ColumnDescriptor<TRow>[];
  fields?: readonly string[];
  filter?: UseResourceListOptions<ResourceTypeName>["filter"];
  order?: UseResourceListOptions<ResourceTypeName>["order"];
  pageSize?: number;
  defaultGroup?: DataViewGroup | null;
  onCreate?: () => void;
  createLabel?: React.ReactNode;
  onRowClick?: (row: TRow) => void;
  onListStateChange?: (state: ListViewState<TRow>) => void;
  rowHref?: (row: TRow) => string;
  emptyMessage?: React.ReactNode;
  className?: string;
}

export function ListView<TRow extends Row = Row>(
  props: ListViewProps<TRow>,
): React.ReactElement {
  const dataView = useDataViewMaybe();
  const initialState = React.useMemo(
    () => ({
      pageSize: props.pageSize,
    }),
    [props.pageSize],
  );
  if (dataView) return <ListViewBody {...props} dataView={dataView} />;
  return (
    <DataViewProvider initialState={initialState}>
      <ListViewBound {...props} />
    </DataViewProvider>
  );
}

function ListViewBound<TRow extends Row = Row>(
  props: ListViewProps<TRow>,
): React.ReactElement {
  return <ListViewBody {...props} dataView={useDataView()} />;
}

function ListViewBody<TRow extends Row = Row>({
  model,
  columns,
  fields,
  filter,
  order,
  pageSize,
  defaultGroup,
  onCreate,
  createLabel,
  onRowClick,
  onListStateChange,
  rowHref,
  emptyMessage = "No records.",
  className,
  dataView,
}: ListViewProps<TRow> & {
  dataView: DataViewContextValue;
}): React.ReactElement {
  const handledDefaultGroupRef = React.useRef<DataViewGroup | null>(null);
  React.useEffect(() => {
    if (!defaultGroup) {
      handledDefaultGroupRef.current = null;
      return;
    }
    if (
      handledDefaultGroupRef.current
      && dataViewGroupsEqual(handledDefaultGroupRef.current, defaultGroup)
    ) {
      return;
    }
    handledDefaultGroupRef.current = defaultGroup;
    if (dataView.state.group === null) dataView.setGroup(defaultGroup);
  }, [dataView.setGroup, dataView.state.group, defaultGroup]);

  const groupDimensions = React.useMemo(
    () => dataView.state.groupStack.map(dataViewGroupToAggregateDimension),
    [dataView.state.groupStack],
  );
  const groupedListMode =
    dataView.state.view === "list" && groupDimensions.length > 0;
  const surface = useDataViewSurface({
    model,
    columns,
    fields,
    filter,
    order,
    pageSize,
    dataView,
    enabled: !groupedListMode,
    onListStateChange,
  });
  const [groupPagerState, setGroupPagerState] =
    React.useState<GroupPagerState | null>(null);
  const handleGroupPagerStateChange = React.useCallback(
    (next: GroupPagerState) => {
      setGroupPagerState((current) =>
        groupPagerStatesEqual(current, next) ? current : next,
      );
    },
    [],
  );
  const toolbarPager = React.useMemo<PagerState>(() => {
    if (!groupedListMode) {
      return {
        total: surface.list.total,
        page: surface.list.page,
        pageSize: surface.list.pageSize,
        hasPrev: surface.list.hasPrev,
        hasNext: surface.list.hasNext,
      };
    }
    // Group-level pager: Pager derives hasPrev/hasNext from page/total.
    return {
      total: groupPagerState?.total ?? 0,
      page: dataView.state.page,
      pageSize: dataView.state.pageSize,
    };
  }, [
    dataView.state.page,
    dataView.state.pageSize,
    groupPagerState?.total,
    groupedListMode,
    surface.list.hasNext,
    surface.list.hasPrev,
    surface.list.page,
    surface.list.pageSize,
    surface.list.total,
  ]);
  const groupOptions = React.useMemo(
    () => buildGroupOptions(columns, defaultGroup),
    [columns, defaultGroup],
  );
  const filterOptions = React.useMemo(
    () => buildFilterOptions(columns, surface.rows),
    [columns, surface.rows],
  );
  const activeFilterIds = activeFilterIdsFor(
    dataView.state.filter,
    filterOptions,
  );

  const setPage = React.useCallback(
    (page: number) => {
      dataView.setPage(page);
    },
    [dataView.setPage],
  );

  const filterText = textFilterValue(dataView.state.filter);
  const interactive = Boolean(onRowClick || rowHref);

  return (
    <div
      className={[
        "overflow-hidden rounded-md border border-border bg-sheet",
        className,
      ]
        .filter(Boolean)
        .join(" ")}
    >
      <DataToolbar
        pager={toolbarPager}
        view={dataView.state.view}
        group={dataView.state.group}
        groupStack={dataView.state.groupStack}
        groupOptions={groupOptions}
        filterOptions={filterOptions}
        visibleFields={surface.visibleFields}
        activeFilterIds={activeFilterIds}
        filterText={filterText}
        createLabel={createLabel ?? createLabelForModel(model)}
        onCreate={onCreate}
        onClearGroup={() => dataView.setGroupStack([])}
        onGroupStackChange={dataView.setGroupStack}
        onVisibleFieldToggle={surface.toggleVisibleField}
        onViewChange={dataView.setView}
        onPageChange={setPage}
        pagerSubject={groupedListMode ? "Groups" : undefined}
        pagerTotalUnit={groupedListMode ? "groups" : undefined}
        onFilterToggle={(id) =>
          dataView.setFilter(
            nextFacetFilter(dataView.state.filter, filterOptions, id),
          )
        }
        onFilterTextChange={(value) =>
          dataView.setFilter(nextTextFilter(dataView.state.filter, value))
        }
      />
      {surface.selectedIds.size > 0 ? (
        <SelectionBar
          count={surface.selectedIds.size}
          onClear={dataView.clearSelectedIds}
        />
      ) : null}
      {groupedListMode ? (
        <GroupedListBody
          model={model}
          table={surface.table}
          tableColumns={surface.tableColumns}
          columnVisibility={surface.columnVisibility}
          visibleColumnCount={surface.visibleColumnCount}
          dataView={dataView}
          groupDimensions={groupDimensions}
          requestedFields={surface.requestedFields}
          mergedFilter={surface.mergedFilter}
          sortOrder={surface.sortOrder}
          order={order}
          interactive={interactive}
          rowHref={rowHref}
          onRowClick={onRowClick}
          emptyMessage={emptyMessage}
          onPagerStateChange={handleGroupPagerStateChange}
        />
      ) : surface.list.error ? (
        <div className="px-3 py-6 text-13 text-danger-text">
          {surface.list.error.message}
        </div>
      ) : dataView.state.view === "board" ? (
        <BoardView
          columns={columns}
          groups={surface.groupedRows}
          dataView={dataView}
          selectedIds={surface.selectedIds}
          interactive={interactive}
          emptyMessage={emptyMessage}
          rowHref={rowHref}
          onRowClick={onRowClick}
        />
      ) : (
        <FlatListBody
          table={surface.table}
          rowModels={surface.rowModels}
          listItems={surface.listItems}
          tableScrollRef={surface.tableScrollRef}
          rowVirtualizer={surface.rowVirtualizer}
          visibleColumnCount={surface.visibleColumnCount}
          allPageSelected={surface.allPageSelected}
          somePageSelected={surface.somePageSelected}
          onPageSelectionChange={surface.setPageSelection}
          dataView={dataView}
          interactive={interactive}
          rowHref={rowHref}
          onRowClick={onRowClick}
          emptyMessage={emptyMessage}
          fetching={surface.list.fetching}
        />
      )}
      {!groupedListMode && surface.list.fetching ? (
        <div className="flex items-center justify-center gap-2 border-t border-border px-3 py-4 text-13 text-fg-muted">
          <Spinner size="sm" />
          Loading...
        </div>
      ) : null}
    </div>
  );
}

function SelectionBar({
  count,
  onClear,
}: {
  count: number;
  onClear: () => void;
}): React.ReactElement {
  return (
    <div className="flex items-center justify-between gap-3 border-b border-border-subtle bg-brand px-3 py-2 text-13 text-on-brand">
      <span>{count} selected</span>
      <Button type="button" variant="ghost" size="sm" onClick={onClear}>
        Clear
      </Button>
    </div>
  );
}

function buildGroupOptions<TRow extends Row>(
  columns: readonly ColumnDescriptor<TRow>[],
  defaultGroup: DataViewGroup | null | undefined,
): readonly DataToolbarGroupOption[] {
  const options: DataToolbarGroupOption[] = [];
  const seen = new Set<string>();
  const addOption = (option: DataToolbarGroupOption) => {
    if (seen.has(option.id)) return;
    seen.add(option.id);
    options.push(option);
  };

  if (defaultGroup) {
    addOption({
      id: defaultGroup.field,
      label: groupFieldLabel(defaultGroup.field),
      group: defaultGroup,
      type: looksLikeDateField(defaultGroup.field) ? "date" : "value",
    });
  }

  for (const column of columns) {
    if (looksLikeDateField(column.field)) {
      addOption({
        id: column.field,
        label: groupFieldLabel(column.field),
        group: { field: column.field, granularity: "day" },
        type: "date",
      });
      continue;
    }
    if (column.field === "status" || column.tone) {
      addOption({
        id: column.field,
        label: column.header ?? groupFieldLabel(column.field),
        group: { field: column.field },
        type: "value",
      });
    }
  }

  return options;
}

function buildFilterOptions<TRow extends Row>(
  columns: readonly ColumnDescriptor<TRow>[],
  rows: readonly TRow[],
): readonly DataToolbarFilterOption[] {
  return columns.flatMap((column) => {
    if (column.field !== "status" && !column.tone) return [];
    return statusValues(column, rows).map((value) => ({
      id: `${column.field}:${value}`,
      label: statusLabel(value),
      chipLabel: statusLabel(value),
      filter: { [column.field]: { exact: value } },
    }));
  });
}

function statusValues<TRow extends Row>(
  column: ColumnDescriptor<TRow>,
  rows: readonly TRow[],
): string[] {
  const values = new Set<string>();
  if (column.tone) {
    for (const key of Object.keys(column.tone)) {
      if (key === key.toUpperCase()) values.add(key);
    }
  }
  if (values.size === 0) {
    for (const row of rows) {
      const value = readPath(row, column.field);
      if (typeof value === "string" && value.trim()) values.add(value);
    }
  }
  return [...values].sort(compareStatusValue);
}

const STATUS_ORDER = ["DRAFT", "IN_REVIEW", "ACTIVE", "ARCHIVED"];

function compareStatusValue(left: string, right: string): number {
  const leftIndex = STATUS_ORDER.indexOf(left.toUpperCase());
  const rightIndex = STATUS_ORDER.indexOf(right.toUpperCase());
  if (leftIndex !== -1 || rightIndex !== -1) {
    return (leftIndex === -1 ? Number.MAX_SAFE_INTEGER : leftIndex)
      - (rightIndex === -1 ? Number.MAX_SAFE_INTEGER : rightIndex);
  }
  return left.localeCompare(right);
}

function activeFilterIdsFor(
  filter: DataViewFilter,
  options: readonly DataToolbarFilterOption[],
): readonly string[] {
  return options.flatMap((option) => {
    const facet = facetFilter(option);
    if (!facet) return [];
    return statusFilterValues(filter, facet.field).includes(facet.value)
      ? [option.id]
      : [];
  });
}

function nextFacetFilter(
  filter: DataViewFilter,
  options: readonly DataToolbarFilterOption[],
  id: string,
): DataViewFilter {
  const option = options.find((candidate) => candidate.id === id);
  const facet = option ? facetFilter(option) : null;
  if (!facet) return filter;
  const current = statusFilterValues(filter, facet.field);
  const nextValues = current.includes(facet.value)
    ? current.filter((value) => value !== facet.value)
    : [...current, facet.value];
  const next = { ...filter };
  if (nextValues.length === 0) {
    delete next[facet.field];
  } else if (nextValues.length === 1) {
    next[facet.field] = { exact: nextValues[0] };
  } else {
    next[facet.field] = { inList: nextValues };
  }
  return next;
}

function facetFilter(
  option: DataToolbarFilterOption,
): { field: string; value: string } | null {
  const entry = Object.entries(option.filter)[0];
  if (!entry) return null;
  const [field, lookup] = entry;
  if (!field || !lookup || typeof lookup !== "object" || Array.isArray(lookup)) {
    return null;
  }
  const exact = (lookup as Record<string, unknown>).exact;
  return typeof exact === "string" ? { field, value: exact } : null;
}

function statusFilterValues(filter: DataViewFilter, field: string): readonly string[] {
  const lookup = filter[field];
  if (!lookup || typeof lookup !== "object" || Array.isArray(lookup)) return [];
  const exact = (lookup as Record<string, unknown>).exact;
  if (typeof exact === "string") return [exact];
  const inList = (lookup as Record<string, unknown>).inList;
  return Array.isArray(inList)
    ? inList.filter((value): value is string => typeof value === "string")
    : [];
}

function textFilterValue(filter: DataViewFilter): string {
  const title = filter.title;
  if (!title || typeof title !== "object" || Array.isArray(title)) return "";
  const value = (title as Record<string, unknown>).iContains;
  return typeof value === "string" ? value : "";
}

function nextTextFilter(filter: DataViewFilter, value: string): DataViewFilter {
  const next = { ...filter };
  const trimmed = value.trim();
  if (trimmed) next.title = { iContains: trimmed };
  else delete next.title;
  return next;
}

function createLabelForModel(model: string): string {
  const name = model.split(".").at(-1) ?? "record";
  return `New ${groupFieldLabel(name).toLowerCase()}`;
}
