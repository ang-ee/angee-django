import * as React from "react";
import type { Row } from "@angee/sdk";

import { Button } from "../ui/button";
import {
  Dialog,
  DialogBackdrop,
  DialogPortal,
  DialogRoot,
} from "../ui/dialog";
import { ListView, type ListColumn, type ListViewProps } from "./ListView";
import { FormView, type FormField, type FormViewProps } from "./FormView";
import type { DataViewGroup } from "./data-view-model";
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
  // A record is open when an id is selected or a create was requested.
  const open = creating || recordId != null;
  const editId = creating ? null : recordId ?? null;

  const handleSaved = React.useCallback(
    (row: Row) => {
      if (typeof row.id === "string") onSelect?.(row.id);
    },
    [onSelect],
  );

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
      className={["grid gap-4 lg:grid-cols-[2fr_1fr]", className]
        .filter(Boolean)
        .join(" ")}
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
