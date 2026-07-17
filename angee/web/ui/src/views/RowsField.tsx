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
import type { ReactElement } from "react";

import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "../ui/table";
import { RequiredMark } from "../ui/label";
import type {
  WidgetDefinition,
  WidgetField,
  WidgetRenderProps,
} from "../widgets/types";
import type { FormSpecFieldDescriptor } from "./form-spec";
import { LabeledDescriptorField } from "./MutationDialog";
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

/** Render the fixed-N rows value as its descriptor-driven edit/read table. */
export function RowsField({
  value,
  field,
  messages = [],
  readOnly = false,
  onChange,
}: WidgetRenderProps<RowsValue>): ReactElement {
  const rows = rowsValue(value);
  const fieldName = rowsFieldName(field);
  const columns = rowTemplate(field);

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
              {columns.map((column) => (
                <RowsCell
                  key={column.name}
                  column={column}
                  fieldName={fieldName}
                  messages={messages}
                  readOnly={readOnly}
                  row={row}
                  rowIndex={rowIndex}
                  onChange={(next) => {
                    if (!onChange) return;
                    onChange(
                      rows.map((current, currentIndex) =>
                        currentIndex === rowIndex
                          ? { ...current, [column.name]: next }
                          : current,
                      ),
                    );
                  }}
                />
              ))}
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

function RowsCell({
  column,
  fieldName,
  messages,
  readOnly,
  row,
  rowIndex,
  onChange,
}: {
  column: FormSpecFieldDescriptor;
  fieldName: string;
  messages: readonly string[];
  readOnly: boolean;
  row: Record<string, unknown>;
  rowIndex: number;
  onChange: (value: unknown) => void;
}): ReactElement {
  const cellPath = `${fieldName}.${rowIndex}.${column.name}`;
  return (
    <TableCell className={readOnly ? "min-w-32 align-top" : "min-w-48 align-top"}>
      <LabeledDescriptorField
        field={{
          ...column,
          name: cellPath,
          label: column.label ?? column.name,
        }}
        value={row[column.name]}
        messages={messagesForDottedPath(messages, cellPath)}
        readOnly={readOnly || column.readOnly}
        showLabel={false}
        showDescription={false}
        onChange={onChange}
      />
    </TableCell>
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
