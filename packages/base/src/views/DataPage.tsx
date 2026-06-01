import * as React from "react";
import type { Row } from "@angee/sdk";
import { ChevronLeft, ChevronRight } from "lucide-react";

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
import {
  DataViewProvider,
  useDataView,
  useDataViewMaybe,
} from "./data-view-context";
import type { DataViewGroup, DataViewKind } from "./data-view-model";
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
  /** Form options forwarded to `FormView`. */
  returning?: FormViewProps["returning"];
  /** Hides the built-in "New" button when the host owns creation. */
  hideCreate?: boolean;
  className?: string;
}

/** A collection list with an open-record form for one model. */
export function DataPage<TRow extends Row = Row>({
  pageSize,
  defaultGroup,
  ...props
}: DataPageProps<TRow>): React.ReactElement {
  const dataView = useDataViewMaybe();
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
    <DataViewProvider
      initialState={{
        pageSize,
        group: defaultGroup ?? null,
      }}
    >
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
  returning,
  hideCreate = false,
  className,
}: DataPageProps<TRow>): React.ReactElement {
  const dataView = useDataView();
  const [listState, setListState] =
    React.useState<ListViewState<TRow> | null>(null);
  const [pendingNavigation, setPendingNavigation] =
    React.useState<PendingRecordNavigation | null>(null);

  // A record is open when an id is selected or a create was requested.
  const open = creating || recordId != null;
  const editId = creating ? null : recordId ?? null;

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
    <ListView<TRow>
      model={model}
      columns={columns}
      fields={fields}
      filter={filter}
      order={order}
      pageSize={pageSize}
      defaultGroup={defaultGroup}
      onCreate={!hideCreate && onSelect ? () => onSelect(null) : undefined}
      onListStateChange={handleListStateChange}
      onRowClick={
        onSelect
          ? (row) => {
              if (typeof row.id === "string") onSelect(row.id);
            }
          : undefined
      }
    />
  );

  const recordForm = open ? (
    <FormView
      model={model}
      id={editId}
      fields={formFields}
      groups={formGroups}
      returning={returning}
      onSaved={handleSaved}
      headerActions={recordHeaderActions}
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
    <div
      className={cn("grid gap-4 lg:grid-cols-[2fr_1fr]", className)}
    >
      <div className="flex flex-col gap-3">
        {list}
      </div>
      {open ? (
        <div className="rounded-md border border-border bg-sheet p-4">
          <div className="mb-3 flex items-center justify-between">
            <h2 className="text-sm font-semibold text-fg">
              {editId == null ? "New record" : "Edit record"}
            </h2>
            <Button variant="ghost" size="sm" onClick={() => onClose?.()}>
              Close
            </Button>
          </div>
          {recordForm}
        </div>
      ) : null}
    </div>
  );
}

interface PendingRecordNavigation {
  page: number;
  edge: "first" | "last";
}

interface RecordNavigation {
  current: number;
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
        <span className="font-medium text-fg">
          {navigation.current.toLocaleString()}
        </span>{" "}
        of {navigation.total.toLocaleString()}
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
  if (index < 0) return null;

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

function rowId(row: Row | undefined): string | null {
  return typeof row?.id === "string" ? row.id : null;
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
