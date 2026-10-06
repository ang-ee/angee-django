import * as React from "react";
import {
  Controller, useFieldArray, useFormState, useWatch,
  type Control, type FieldValues, type UseFormSetValue,
} from "react-hook-form";
import type { CrudFilter } from "@refinedev/core";
import {
  defaultWidgetForModelField, filterFieldType, useSchemaFieldMetadata,
  type DataResourceLinesMetadata, type Row,
} from "@angee/metadata";

import { Glyph } from "../../chrome/Glyph";
import { useUiT } from "../../i18n";
import { titleCase } from "../../lib/titleCase";
import { Badge } from "../../ui/badge";
import { Button } from "../../ui/button";
import { relationValueId, type WidgetControlProps } from "../../widgets/types";
import { RowsListView } from "../resource/RowsListView";
import { defineRowAction } from "../resource/RowActions";
import {
  enumOptions, relationFieldInfoForField, relationListFieldInfoForField,
} from "../resource/model-metadata-defaults";
import type { ColumnDescriptor, FieldDescriptor } from "../page";
import { RelationFieldWidget } from "../relation/RelationFieldWidget";
import { RelationMultiFieldWidget } from "../relation/RelationMultiFieldWidget";
import { relationSelectedOption } from "../relation/relation-options";
import {
  CLIENT_LINE_KEY, duplicateLineRow, emptyLineRow, lineDiffConfig, lineLockedFields,
} from "./editable-lines";
import { FieldDescriptorControl } from "./field-descriptor-control";
import type { ValidationErrors } from "./validation-errors";

export interface EditableLinesProps {
  /** Owns the ordered child array in the composing form's values and save diff. */
  control: Control<FieldValues>;
  /** Native leaf writer for widget-produced row patches. */
  setValue: UseFormSetValue<FieldValues>;
  /** Form field holding the ordered child lines — the linesResource.field. */
  name: string;
  lines: DataResourceLinesMetadata;
  /** Owning document, passed to domain widgets without interpreting its fields. */
  parentRow?: Row | null;
  readOnly?: boolean;
  /** Document totals supplied by the composer, recomputed from live line rows. */
  footer?: (rows: readonly Row[]) => React.ReactNode;
  /** Server validation messages per line row, indexed by row position. */
  rowErrors?: readonly (ValidationErrors | undefined)[];
  /** Initially visible editable fields; others remain available in the header menu and save diff. */
  primaryFields?: readonly string[];
  /**
   * Authored presentation for named line fields — header, help text, choices or
   * widget — over the child metadata. Every line field still renders from its
   * metadata; help text lists under the lines, so a column kept behind the header
   * menu still explains itself.
   */
  fields?: readonly EditableLineField[];
  /** Read-only projections supplied by the composing domain. */
  supplementalColumns?: readonly EditableLineSupplementalColumn[];
  /** Domain-owned relation constraints for a field and its owning document. */
  relationFilters?: (
    fieldName: string,
    parentRow: Row | null,
  ) => readonly CrudFilter[] | undefined;
}

/** Authored presentation of one line field, keyed by its name. */
export interface EditableLineField extends Pick<FieldDescriptor, "name" | "description" | "options" | "widget"> {
  /** Column header and the cells' accessible name. */
  label?: string;
}

export interface EditableLineSupplementalColumn {
  key: string;
  header: React.ReactNode;
  minWidth?: number;
  /** Computed amount columns align right by default. */
  align?: "left" | "right";
  render: (
    row: Row,
    parentRow: Row | null,
    index: number,
    context: { formIsDirty: boolean },
  ) => React.ReactNode;
}

/** Table identity is the RHF key; widgets and save diffs retain the child's public id. */
type LineViewRow = { id: string; index: number; value: Row };

/**
 * The local data view in edit mode, bound to the parent form's native field array.
 * A row the backend locks (`lines.lockField`) is a system row: the list shows a lock
 * in place of its reorder handle, it carries a "System" marker, it offers no duplicate
 * or remove, and its locked cells are read-only while its other cells stay editable.
 */
export function EditableLines({
  control, setValue, name, lines, parentRow, readOnly, footer, rowErrors,
  primaryFields, fields: authoredFields = [], supplementalColumns = [], relationFilters,
}: EditableLinesProps): React.ReactElement {
  const t = useUiT();
  const controlId = React.useId();
  const config = React.useMemo(() => lineDiffConfig(lines), [lines]);
  const schemaMetadata = useSchemaFieldMetadata();
  // Keep RHF's presentation identity off the child's own public id.
  const { fields, append, insert, move, remove } = useFieldArray({ control, name, keyName: "rhfKey" });
  const { isDirty: formIsDirty } = useFormState({ control });
  const rows = (useWatch({ control, name }) as Row[] | undefined) ?? [];
  React.useEffect(() => {
    fields.forEach((field, index) => {
      const row = rows[index];
      if (row && row[config.idField] == null && row[CLIENT_LINE_KEY] == null) {
        setValue<string>(`${name}.${index}.${CLIENT_LINE_KEY}`, field.rhfKey, { shouldDirty: false });
      }
    });
  }, [config.idField, fields, name, rows, setValue]);
  // Async widgets resolve their RHF identity at completion, never a captured index.
  const latest = React.useRef({ fields, readOnly, setValue });
  latest.current = { fields, readOnly, setValue };
  const mounted = React.useRef(true);
  React.useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);
  const patchRow = React.useCallback((key: string, patch: Record<string, unknown>) => {
    const current = latest.current;
    if (!mounted.current || current.readOnly) return;
    const index = current.fields.findIndex((field) => field.rhfKey === key);
    if (index < 0) return;
    for (const [field, value] of Object.entries(patch)) {
      current.setValue<string>(`${name}.${index}.${field}`, value, { shouldDirty: true });
    }
  }, [name]);
  const viewRows = React.useMemo(() => fields.map((field, index) => ({
    id: field.rhfKey, index, value: rows[index] ?? {},
  })), [fields, rows]);
  const editableFields = (lines.fields ?? []).filter((field) => field.name !== config.positionField);
  const primary = primaryFields?.some((name) => editableFields.some((field) => field.name === name))
    ? new Set(primaryFields) : null;
  const authoredByName = new Map(authoredFields.map((field) => [field.name, field]));
  const columns: ColumnDescriptor<LineViewRow>[] = editableFields.map((field) => {
    const authored = authoredByName.get(field.name);
    const widget = authored?.widget ?? defaultWidgetForModelField(field);
    const customWidget = Boolean(field.widget && !["many2one", "many2many"].includes(field.widget));
    const relation = customWidget ? null : relationFieldInfoForField(field, schemaMetadata);
    const relationMulti = customWidget ? null : relationListFieldInfoForField(field, schemaMetadata);
    const header = authored?.label ?? titleCase(field.name);
    const descriptor: FieldDescriptor = {
      name: field.name, label: header, widget, options: authored?.options ?? enumOptions(field),
      ...(field.currencyField ? { currencyField: field.currencyField } : {}),
    };
    const hasErrors = rowErrors?.some((error) => rowMessages(error, field.name).length > 0);
    return {
      id: field.name, field: `value.${field.name}`, header, widget,
      ...(field.currencyField ? { currencyField: field.currencyField } : {}),
      sortable: false, interactive: true,
      hiddenByDefault: Boolean(primary && !primary.has(field.name)),
      // Validation reveals an optional field even if the user previously hid it.
      hideable: !hasErrors,
      align: filterFieldType(field.name, field) === "number"
        || ["money", "integer", "float"].includes(widget ?? "") ? "right" : "left",
      render: ({ id, index, value }) => {
        const messages = rowMessages(rowErrors?.[index], field.name);
        const cellReadOnly = readOnly || lineLockedFields(value, config).includes(field.name);
        const controlProps: WidgetControlProps = {
          presentation: "cell", id: `${controlId}-${id}-${field.name}`,
          "aria-invalid": messages.length > 0 || undefined,
        };
        return <div className="min-w-0">
          <Controller control={control} name={`${name}.${index}.${field.name}`}
            render={({ field: controller }) => relationMulti ? (
              <RelationMultiFieldWidget
                controlProps={controlProps}
                value={Array.isArray(controller.value) ? controller.value : []}
                onChange={controller.onChange} readOnly={cellReadOnly} relation={relationMulti}
                filters={relationFilters?.(field.name, parentRow ?? null)}
                aria-label={header}
              />
            ) : relation ? (
              <RelationFieldWidget
                controlProps={controlProps} controlRef={controller.ref}
                value={relationValueId(controller.value) || null}
                onChange={controller.onChange} readOnly={cellReadOnly} relation={relation}
                filters={relationFilters?.(field.name, parentRow ?? null)}
                selectedOption={relationSelectedOption(controller.value, relation.labelField)}
                aria-label={header}
              />
            ) : (
              <FieldDescriptorControl
                controlProps={controlProps} controlRef={controller.ref} field={descriptor}
                row={value} parentRow={parentRow} value={controller.value}
                messages={messages} readOnly={cellReadOnly} onChange={controller.onChange}
                onRowChange={(patch) => patchRow(id, patch)}
              />
            )}
          />
          {messages.map((message, index) => (
            <p key={index} className="mt-1 text-xs text-danger-text">{message}</p>
          ))}
        </div>;
      },
    };
  });
  columns.push(...supplementalColumns.map((column): ColumnDescriptor<LineViewRow> => ({
    id: column.key, field: column.key, header: column.header,
    minWidth: column.minWidth, align: column.align ?? "right", sortable: false,
    render: ({ value, index }) => column.render(value, parentRow ?? null, index, { formIsDirty }),
  })));
  const described = editableFields.flatMap((field) => {
    const authored = authoredByName.get(field.name);
    return authored?.description == null ? [] : [{
      name: field.name, label: authored.label ?? titleCase(field.name), description: authored.description,
    }];
  });
  const unlocked = ({ value }: LineViewRow) => lineLockedFields(value, config).length === 0;
  if (config.lockField) {
    columns.push({
      id: "system", field: "system", header: t("lines.system"), headerVisuallyHidden: true,
      sortable: false, hideable: false,
      render: (row) => unlocked(row) ? null : (
        <Badge tone="neutral" density="compact" title={t("lines.systemHint")}>{t("lines.system")}</Badge>
      ),
    });
  }
  const rowActions = readOnly ? undefined : [
    defineRowAction<LineViewRow>({
      kind: "page", id: "duplicate", label: t("lines.duplicate"), icon: "copy",
      presentation: "icon", variant: "ghost", pendingPolicy: "disable-actions", visible: unlocked,
      onSelect: ({ index }) => insert(index + 1, duplicateLineRow(rows[index] ?? {}, config) as never),
    }),
    defineRowAction<LineViewRow>({
      kind: "page", id: "remove", label: t("lines.remove"), icon: "trash",
      presentation: "icon", variant: "danger", pendingPolicy: "disable-actions", visible: unlocked,
      onSelect: ({ index }) => remove(index),
    }),
  ];
  return <div className="min-w-0">
    <RowsListView<LineViewRow>
      rows={viewRows} columns={columns} rowActions={rowActions}
      presentation="embedded" scope="local" headerVisibility="visible"
      pageSize={Number.MAX_SAFE_INTEGER /* Keep the ordered child array on one page as rows are added. */}
      emptyContent={t("lines.empty")}
      onReorder={readOnly ? undefined : (fromId, toId) => {
        const current = latest.current;
        if (current.readOnly) return;
        const from = current.fields.findIndex((field) => field.rhfKey === fromId);
        const to = current.fields.findIndex((field) => field.rhfKey === toId);
        if (from >= 0 && to >= 0 && from !== to) move(from, to);
      }}
      canReorderRow={unlocked}
      footerRow={readOnly ? undefined : <Button type="button" variant="ghost" size="sm"
        onClick={() => append(emptyLineRow(fields.length, config) as never)}>
        <Glyph name="plus" decorative />{t("lines.add")}
      </Button>}
    />
    {described.length > 0 ? (
      <dl className="grid gap-1 px-2 pt-2 text-xs text-fg-muted">
        {described.map(({ name, label, description }) => (
          <div key={name}>
            <dt className="inline font-medium text-fg-2">{label}</dt>{": "}
            <dd className="inline">{description}</dd>
          </div>
        ))}
      </dl>
    ) : null}
    {footer ? <div className="pt-2">{footer(rows)}</div> : null}
  </div>;
}

function rowMessages(
  rowError: ValidationErrors | undefined,
  fieldName: string,
): readonly string[] {
  return rowError?.fieldErrors[fieldName] ?? [];
}
