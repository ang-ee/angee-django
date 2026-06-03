import * as React from "react";
import {
  useResourceList,
  type Row,
} from "@angee/sdk";
import { ChevronLeft, ChevronRight } from "lucide-react";

import {
  useBreadcrumb,
  type BreadcrumbItem,
} from "../chrome/Breadcrumb";
import { cn } from "../lib/cn";
import { DataViewSwitcher } from "../toolbars";
import { Button } from "../ui/button";
import {
  Dialog,
  DialogBackdrop,
  DialogPortal,
  DialogRoot,
} from "../ui/dialog";
import {
  ListView,
  type ListColumn,
  type ListViewProps,
  type ListViewState,
} from "./ListView";
import { FormView, type FormField, type FormViewProps } from "./FormView";
import { readPath } from "./list-internals";
import {
  DataViewProvider,
  useDataView,
  useDataViewMaybe,
} from "./data-view-context";
import {
  dataViewSortToResourceOrder,
  type DataViewFilter,
  type DataViewGroup,
  type DataViewKind,
} from "./data-view-model";
import type { GroupDescriptor } from "./page";

/** Where the open record's form renders relative to the list. */
export type RecordPlacement = "inline" | "drawer";

export interface DataPageProps<TRow extends Row = Row> {
  /** Model label, e.g. `"notes.Note"`, shared by the list and the form. */
  model: string;
  /** Columns for the list. */
  columns: readonly ListColumn<TRow>[];
  /** Fields for the record form. */
  formFields: readonly FormField[];
  formGroups?: readonly GroupDescriptor[];
  /** Currently open record id; `"new"` (or the `creating` flag) opens a blank form. */
  recordId?: string | null;
  /** True when creating a new record (an alternative to `recordId === null`). */
  creating?: boolean;
  /** Called to open a record (or `null` to start a create). */
  onSelect?: (id: string | null) => void;
  /** Called to dismiss the open record. */
  onClose?: () => void;
  /** Where the form shows: beside/below the list (`"inline"`) or in a modal. */
  placement?: RecordPlacement;
  /** List options forwarded to `ListView`. */
  filter?: ListViewProps<TRow>["filter"];
  order?: ListViewProps<TRow>["order"];
  pageSize?: number;
  defaultGroup?: DataViewGroup | null;
  fields?: ListViewProps<TRow>["fields"];
  /** List component used for the collection surface. Defaults to the lean flat list. */
  list?: React.ComponentType<
    ListViewProps<TRow> & { defaultGroup?: DataViewGroup | null }
  >;
  /** Form options forwarded to `FormView`. */
  returning?: FormViewProps["returning"];
  /** Hides the built-in "New" button when the host owns creation. */
  hideCreate?: boolean;
  rowHref?: (row: TRow) => string;
  className?: string;
}

/** A collection list with an open-record form for one model. */
export function DataPage<TRow extends Row = Row>({
  pageSize,
  defaultGroup,
  ...props
}: DataPageProps<TRow>): React.ReactElement {
  const dataView = useDataViewMaybe();
  const initialState = React.useMemo(
    () => ({
      pageSize,
    }),
    [pageSize],
  );

  if (dataView) {
    return (
      <DataPageBody
        {...props}
        pageSize={pageSize}
        defaultGroup={defaultGroup}
      />
    );
  }

  return (
    <DataViewProvider initialState={initialState}>
      <DataPageBody
        {...props}
        pageSize={pageSize}
        defaultGroup={defaultGroup}
      />
    </DataViewProvider>
  );
}

function DataPageBody<TRow extends Row = Row>({
  model,
  columns,
  formFields,
  formGroups,
  recordId,
  creating = false,
  onSelect,
  onClose,
  placement = "inline",
  filter,
  order,
  pageSize,
  defaultGroup,
  fields,
  list: ListComponent = ListView as React.ComponentType<
    ListViewProps<TRow> & { defaultGroup?: DataViewGroup | null }
  >,
  returning,
  hideCreate = false,
  rowHref,
  className,
}: DataPageProps<TRow>): React.ReactElement {
  const dataView = useDataView();
  const { items: breadcrumbItems, setItems: setBreadcrumbItems } =
    useBreadcrumb();
  const baseTrailRef = React.useRef<readonly BreadcrumbItem[] | null>(null);
  if (baseTrailRef.current === null) {
    baseTrailRef.current = breadcrumbItems.length > 0
      ? breadcrumbItems
      : [{ label: collectionLabelForModel(model) }];
  }
  const [listState, setListState] =
    React.useState<ListViewState<TRow> | null>(null);
  const [pendingNavigation, setPendingNavigation] =
    React.useState<PendingRecordNavigation | null>(null);

  // A record is open when an id is selected or a create was requested.
  const open = creating || recordId != null;
  const editId = creating ? null : recordId ?? null;
  // `defaultGroup` is forwarded to the list component (below); GroupListView is
  // its sole owner/seeder. The lean ListView ignores it, so a flat page never
  // seeds group state.
  const recordBreadcrumb = React.useMemo(
    () =>
      recordBreadcrumbLabel({
        columns,
        creating,
        formFields,
        listState,
        model,
        recordId,
      }),
    [columns, creating, formFields, listState, model, recordId],
  );

  React.useEffect(() => {
    const baseTrail = baseTrailRef.current ?? [
      { label: collectionLabelForModel(model) },
    ];
    setBreadcrumbItems(
      open ? [...baseTrail, { label: recordBreadcrumb }] : baseTrail,
    );
  }, [model, open, recordBreadcrumb, setBreadcrumbItems]);

  React.useEffect(
    () => () => {
      setBreadcrumbItems(
        baseTrailRef.current ?? [{ label: collectionLabelForModel(model) }],
      );
    },
    [model, setBreadcrumbItems],
  );

  const handleListStateChange = React.useCallback(
    (next: ListViewState<TRow>) => {
      setListState((current) =>
        listStatesEqual(current, next) ? current : next,
      );
    },
    [],
  );

  const handleSaved = React.useCallback(
    (row: Row) => {
      if (typeof row.id === "string") onSelect?.(row.id);
    },
    [onSelect],
  );

  React.useEffect(() => {
    if (!pendingNavigation || !listState || listState.fetching) return;
    if (pendingNavigation.page !== listState.page) return;

    const target =
      pendingNavigation.edge === "first"
        ? listState.rows[0]
        : listState.rows[listState.rows.length - 1];
    const targetId = rowId(target);
    if (targetId) {
      setPendingNavigation(null);
      onSelect?.(targetId);
    } else if (listState.rows.length === 0) {
      setPendingNavigation(null);
    }
  }, [listState, onSelect, pendingNavigation]);

  const recordNavigation = React.useMemo(
    () =>
      buildRecordNavigation({
        creating,
        listState,
        recordId,
        onSelect,
        setPage: dataView.setPage,
        setPendingNavigation,
      }),
    [creating, dataView.setPage, listState, onSelect, recordId],
  );

  const recordHeaderActions = open ? (
    <RecordHeaderActions
      view={dataView.state.view}
      navigation={recordNavigation}
      onViewChange={(view) => {
        dataView.setView(view);
        onClose?.();
      }}
    />
  ) : null;
  const list = (
    <ListComponent
      model={model}
      columns={columns}
      fields={fields}
      filter={filter}
      order={order}
      pageSize={pageSize}
      defaultGroup={defaultGroup}
      onCreate={!hideCreate && onSelect ? () => onSelect(null) : undefined}
      onListStateChange={handleListStateChange}
      rowHref={rowHref}
      onRowClick={
        onSelect
          ? (row) => {
              if (typeof row.id === "string") onSelect(row.id);
            }
          : undefined
      }
    />
  );
  const listStateOnly = open ? (
    <ListStateProbe<TRow>
      model={model}
      columns={columns}
      fields={fields}
      filter={filter}
      order={order}
      pageSize={pageSize}
      dataView={dataView}
      onListStateChange={handleListStateChange}
    />
  ) : null;

  const recordForm = open ? (
    <FormView
      model={model}
      id={editId}
      fields={formFields}
      groups={formGroups}
      returning={returning}
      onSaved={handleSaved}
      toolbar={recordHeaderActions}
    />
  ) : null;

  if (placement === "drawer") {
    return (
      <div className={["flex flex-col gap-3", className].filter(Boolean).join(" ")}>
        {list}
        <DialogRoot
          open={open}
          onOpenChange={(next) => {
            if (!next) onClose?.();
          }}
        >
          <DialogPortal>
            <DialogBackdrop />
            <Dialog.Content size="md" className="p-5">
              {recordForm}
            </Dialog.Content>
          </DialogPortal>
        </DialogRoot>
      </div>
    );
  }

  return (
    <div className={cn("min-w-0", className)}>
      {open ? (
        <>
          {listStateOnly}
          <div className="overflow-hidden rounded-md border border-border bg-sheet">
            {recordForm}
          </div>
        </>
      ) : (
        list
      )}
    </div>
  );
}

interface PendingRecordNavigation {
  page: number;
  edge: "first" | "last";
}

function ListStateProbe<TRow extends Row>({
  model,
  columns,
  fields,
  filter,
  order,
  pageSize,
  dataView,
  onListStateChange,
}: {
  model: string;
  columns: readonly ListColumn<TRow>[];
  fields?: ListViewProps<TRow>["fields"];
  filter?: ListViewProps<TRow>["filter"];
  order?: ListViewProps<TRow>["order"];
  pageSize?: number;
  dataView: ReturnType<typeof useDataView>;
  onListStateChange: (state: ListViewState<TRow>) => void;
}): null {
  React.useEffect(() => {
    if (pageSize && dataView.state.pageSize !== pageSize) {
      dataView.setPageSize(pageSize);
    }
  }, [dataView.setPageSize, dataView.state.pageSize, pageSize]);

  const requestedFields = React.useMemo(() => {
    const paths = new Set<string>(["id"]);
    for (const column of columns) paths.add(column.field);
    for (const extra of fields ?? []) paths.add(extra);
    return [...paths];
  }, [columns, fields]);
  const mergedFilter = React.useMemo(
    () => mergeFilters(filter, dataView.state.filter),
    [dataView.state.filter, filter],
  );
  const sortOrder = dataViewSortToResourceOrder(dataView.state.sort);
  const list = useResourceList(model, {
    fields: requestedFields,
    filter: mergedFilter,
    order: sortOrder ?? order,
    pageSize: dataView.state.pageSize,
    page: dataView.state.page,
  });
  const rows = list.rows as readonly TRow[];
  const listState = React.useMemo<ListViewState<TRow>>(
    () => ({
      rows,
      total: list.total,
      page: list.page,
      pageSize: list.pageSize,
      pageCount: list.pageCount,
      hasNext: list.hasNext,
      hasPrev: list.hasPrev,
      fetching: list.fetching,
    }),
    [
      rows,
      list.total,
      list.page,
      list.pageSize,
      list.pageCount,
      list.hasNext,
      list.hasPrev,
      list.fetching,
    ],
  );
  React.useEffect(() => {
    onListStateChange(listState);
  }, [listState, onListStateChange]);
  return null;
}

interface RecordNavigation {
  /** Undefined when the open record isn't in the loaded slice (grouped/deep). */
  current?: number;
  total: number;
  onPrev?: () => void;
  onNext?: () => void;
}

function RecordHeaderActions({
  view,
  navigation,
  onViewChange,
}: {
  view: DataViewKind;
  navigation: RecordNavigation | null;
  onViewChange: (view: DataViewKind) => void;
}): React.ReactElement {
  return (
    <>
      {navigation ? <RecordPager navigation={navigation} /> : null}
      <DataViewSwitcher
        view={view}
        ariaLabel="Record view switcher"
        onViewChange={onViewChange}
      />
    </>
  );
}

function RecordPager({
  navigation,
}: {
  navigation: RecordNavigation;
}): React.ReactElement {
  return (
    <nav
      aria-label="Record navigation"
      className="flex items-center gap-2 text-13 text-fg-muted"
    >
      <span className="whitespace-nowrap tabular-nums">
        {navigation.current !== undefined ? (
          <>
            <span className="font-medium text-fg">
              {navigation.current.toLocaleString()}
            </span>{" "}
            of {navigation.total.toLocaleString()}
          </>
        ) : (
          <>of {navigation.total.toLocaleString()}</>
        )}
      </span>
      <div className="flex items-center gap-1">
        <Button
          type="button"
          variant="ghost"
          size="iconSm"
          aria-label="Previous record"
          disabled={!navigation.onPrev}
          onClick={navigation.onPrev}
        >
          <ChevronLeft className="glyph" aria-hidden />
        </Button>
        <Button
          type="button"
          variant="ghost"
          size="iconSm"
          aria-label="Next record"
          disabled={!navigation.onNext}
          onClick={navigation.onNext}
        >
          <ChevronRight className="glyph" aria-hidden />
        </Button>
      </div>
    </nav>
  );
}

function buildRecordNavigation<TRow extends Row>({
  creating,
  listState,
  recordId,
  onSelect,
  setPage,
  setPendingNavigation,
}: {
  creating: boolean;
  listState: ListViewState<TRow> | null;
  recordId?: string | null;
  onSelect?: (id: string | null) => void;
  setPage: (page: number) => void;
  setPendingNavigation: React.Dispatch<
    React.SetStateAction<PendingRecordNavigation | null>
  >;
}): RecordNavigation | null {
  if (creating || typeof recordId !== "string" || !listState) return null;
  const index = listState.rows.findIndex((row) => rowId(row) === recordId);
  if (index < 0) {
    // The open record isn't in the loaded slice (e.g. a grouped list or a deep
    // record). Keep the pager visible with the filtered total; page-local
    // Prev/Next can't resolve neighbors here, so they stay disabled.
    return { total: listState.total ?? listState.rows.length };
  }

  const current = (listState.page - 1) * listState.pageSize + index + 1;
  const total = listState.total ?? Math.max(current, listState.rows.length);
  const prevId = rowId(listState.rows[index - 1]);
  const nextId = rowId(listState.rows[index + 1]);
  const canPrevPage = listState.hasPrev && listState.page > 1;
  const canNextPage =
    listState.hasNext &&
    (listState.total === undefined || current < listState.total);

  return {
    current,
    total,
    onPrev:
      onSelect && prevId
        ? () => onSelect(prevId)
        : onSelect && canPrevPage
          ? () => {
              const page = Math.max(1, listState.page - 1);
              setPendingNavigation({ page, edge: "last" });
              setPage(page);
            }
          : undefined,
    onNext:
      onSelect && nextId
        ? () => onSelect(nextId)
        : onSelect && canNextPage
          ? () => {
              const page = listState.page + 1;
              setPendingNavigation({ page, edge: "first" });
              setPage(page);
            }
          : undefined,
  };
}

function createLabelForModel(model: string): string {
  const name = model.split(".").at(-1) ?? "record";
  return `New ${titleCase(name).toLowerCase()}`;
}

function collectionLabelForModel(model: string): string {
  const name = model.split(".").at(-1) ?? "records";
  return `${titleCase(name)}s`;
}

function recordBreadcrumbLabel<TRow extends Row>({
  columns,
  creating,
  formFields,
  listState,
  model,
  recordId,
}: {
  columns: readonly ListColumn<TRow>[];
  creating: boolean;
  formFields: readonly FormField[];
  listState: ListViewState<TRow> | null;
  model: string;
  recordId?: string | null;
}): React.ReactNode {
  if (creating) return createLabelForModel(model);
  const row = typeof recordId === "string"
    ? listState?.rows.find((candidate) => rowId(candidate) === recordId)
    : undefined;
  const titlePath =
    formFields.find((field) => field.title)?.name ?? columns[0]?.field;
  const value = titlePath && row ? readPath(row, titlePath) : null;
  return breadcrumbValue(value) ?? titleCase(model.split(".").at(-1) ?? "Record");
}

function breadcrumbValue(value: unknown): React.ReactNode | null {
  if (typeof value === "string" && value.trim()) return value;
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  if (typeof value === "boolean") return value ? "Yes" : "No";
  return null;
}

function titleCase(value: string): string {
  return value
    .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
    .replace(/[_-]+/g, " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function rowId(row: Row | undefined): string | null {
  return typeof row?.id === "string" ? row.id : null;
}

function mergeFilters<TRow extends Row>(
  base: ListViewProps<TRow>["filter"],
  view: DataViewFilter,
): ListViewProps<TRow>["filter"] {
  if (!base) return Object.keys(view).length > 0 ? view : undefined;
  return { ...base, ...view };
}

function listStatesEqual<TRow extends Row>(
  left: ListViewState<TRow> | null,
  right: ListViewState<TRow>,
): boolean {
  if (!left) return false;
  return (
    rowIdsEqual(left.rows, right.rows) &&
    left.total === right.total &&
    left.page === right.page &&
    left.pageSize === right.pageSize &&
    left.pageCount === right.pageCount &&
    left.hasNext === right.hasNext &&
    left.hasPrev === right.hasPrev &&
    left.fetching === right.fetching
  );
}

function rowIdsEqual(
  left: readonly Row[],
  right: readonly Row[],
): boolean {
  if (left.length !== right.length) return false;
  return left.every((row, index) => rowId(row) === rowId(right[index]));
}
