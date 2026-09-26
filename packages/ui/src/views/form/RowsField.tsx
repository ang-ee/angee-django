/**
 * Fixed-N array-of-objects composition for the `rows` widget. The server owns
 * row count; this view edits cells in place through immutable controlled-value
 * updates, renders every cell through the shared labeled descriptor/relation
 * path, and scopes validation by dotted paths such as `rows.0.target`.
 *
 * This is deliberately distinct from {@link EditableLines}: `RowsField` maps a
 * fixed server-computed value, while `EditableLines` owns variable-N document
 * lines through react-hook-form's `useFieldArray` and add/remove/reorder actions.
 * Relation cells here retain their form-spec relation config; the divergence
 * from EditableLines' metadata-derived relation-cell path is tracked for later
 * reconciliation outside this fixed-N contract.
 */
import type { ReactElement, ReactNode } from "react";

import { DetailSection } from "../../fragments/DetailSurface";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "../../ui/table";
import { RequiredMark } from "../../ui/label";
import type {
  WidgetDefinition,
  WidgetField,
  WidgetRenderProps,
} from "../../widgets/types";
import type { FormSpecFieldDescriptor } from "./form-spec";
import { LabeledDescriptorField } from "./MutationDialog";
import { updatedRecord } from "./field-values";
import { isCompositeFieldDescriptor, isFieldVisible } from "./form-view-model";
import { messagesForDottedPath } from "./validation-errors";

export type RowsValue = readonly Record<string, unknown>[];

type RowsWidgetField = WidgetField & {
  rowTemplate?: readonly FormSpecFieldDescriptor[];
};

function RowsEdit(props: WidgetRenderProps<RowsValue>): ReactElement {
  return <RowsField {...props} />;
}

function RowsRead(props: WidgetRenderProps<RowsValue>): ReactElement {
  return <RowsField {...props} readOnly />;
}

/**
 * Render fixed-N object rows as a table, or titled sections with rowTitle.
 * Both layouts evaluate showWhen against each row. Tables retain empty cells
 * for hidden fields so the remaining values stay under their column headers.
 */
export function RowsField({
  value,
  field,
  messages = [],
  readOnly = false,
  onChange,
  onCommit,
  controlRef,
  rowTitle,
}: WidgetRenderProps<RowsValue> & {
  rowTitle?: (row: Readonly<Record<string, unknown>>, index: number) => ReactNode;
}): ReactElement {
  const rows = rowsValue(value);
  const fieldName = rowsFieldName(field);
  const columns = rowTemplate(field).filter((column) =>
    !column.hidden && (!rows.length || rows.some((row) => isFieldVisible(column, row))),
  );
  const rowFields = (row: Record<string, unknown>, rowIndex: number) => {
    const visibleColumns = columns.filter((column) => isFieldVisible(column, row));
    const focusColumn = visibleColumns.find((column) => !column.readOnly);
    return (rowTitle ? visibleColumns : columns).map((column) => {
      const cellPath = `${fieldName}.${rowIndex}.${column.name}`;
      const control = visibleColumns.includes(column) ? <LabeledDescriptorField
        field={{ ...column, name: cellPath, label: column.label ?? column.name }}
        value={row[column.name]}
        dialogValues={row}
        messages={messagesForDottedPath(messages, cellPath)}
        readOnly={readOnly || column.readOnly}
        showLabel={Boolean(rowTitle)}
        showDescription={Boolean(rowTitle)}
        onChange={(next) => onChange?.(rows.map((current, currentIndex) =>
          currentIndex === rowIndex ? updatedRecord(current, column.name, next) : current,
        ))}
        onCommit={onCommit}
        controlRef={rowIndex === 0 && column === focusColumn ? controlRef : undefined}
      /> : null;
      return rowTitle
        ? <div key={column.name} className={isCompositeFieldDescriptor(column) ? "md:col-span-2" : undefined}>{control}</div>
        : <TableCell key={column.name} className={readOnly ? "min-w-32 align-top" : "min-w-48 align-top"}>{control}</TableCell>;
    });
  };

  if (rowTitle) {
    return <div
      role="group"
      id={field?.controlProps?.id}
      aria-labelledby={field?.controlProps?.["aria-labelledby"]}
      aria-label={field?.controlProps?.["aria-labelledby"] ? undefined : typeof field?.label === "string" ? field.label : fieldName}
      aria-describedby={field?.controlProps?.["aria-describedby"]}
      className="space-y-4"
    >
      {rows.map((row, rowIndex) => <DetailSection key={rowIndex} title={rowTitle(row, rowIndex)} density="sm">
        <div className="grid gap-4 md:grid-cols-2">{rowFields(row, rowIndex)}</div>
      </DetailSection>)}
    </div>;
  }

  return (
    <div className="overflow-x-auto rounded-6 border border-border">
      <Table
        id={field?.controlProps?.id}
        aria-label={
          typeof field?.label === "string" ? field.label : fieldName
        }
        aria-describedby={field?.controlProps?.["aria-describedby"]}
        density={readOnly ? "compact" : "comfortable"}
        className="min-w-max"
      >
        <TableHeader>
          <TableRow>
            {columns.map((column) => (
              <TableHead
                key={column.name}
                scope="col"
                title={
                  typeof column.description === "string"
                    ? column.description
                    : undefined
                }
              >
                {column.label ?? column.name}
                <RequiredMark required={column.required} className="ml-1" />
              </TableHead>
            ))}
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((row, rowIndex) => (
            // The server fixes N in v1, so the index is a stable row identity:
            // rows are edited in place and are never inserted, removed, or sorted.
            <TableRow key={rowIndex}>
              {rowFields(row, rowIndex)}
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

function rowsFieldName(field: WidgetField | undefined): string {
  if (!field?.name) {
    throw new Error('The "rows" widget requires a descriptor field name.');
  }
  return field.name;
}

function rowTemplate(
  field: WidgetField | undefined,
): readonly FormSpecFieldDescriptor[] {
  const descriptorField = field as RowsWidgetField | undefined;
  if (!descriptorField?.rowTemplate) {
    throw new Error('The "rows" widget requires field.rowTemplate.');
  }
  return descriptorField.rowTemplate;
}

function rowsValue(value: unknown): RowsValue {
  if (value == null) return [];
  if (
    !Array.isArray(value) ||
    value.some(
      (row) => !row || typeof row !== "object" || Array.isArray(row),
    )
  ) {
    throw new Error('The "rows" widget value must be an array of objects.');
  }
  return value as RowsValue;
}

export const rowsWidget = {
  edit: RowsEdit,
  read: RowsRead,
} satisfies WidgetDefinition<RowsValue>;
