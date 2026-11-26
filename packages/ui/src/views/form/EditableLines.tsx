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
import { Chip } from "../../ui/chip";
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
import type { FormHistory } from "./use-form-history";
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
   * Authored presentation for named line fields — header, help text, choices,
   * widget, currency path, or status display — over the child metadata. Every
   * line field still renders from its metadata; help text lists under the lines,
   * so a column kept behind the header menu still explains itself.
   */
  fields?: readonly EditableLineField[];
  /** Read-only projections supplied by the composing domain. */
  supplementalColumns?: readonly EditableLineSupplementalColumn[];
  /** Domain-owned relation constraints for a field and its owning document. */
  relationFilters?: (
    fieldName: string,
    parentRow: Row | null,
  ) => readonly CrudFilter[] | undefined;
  /**
   * The composing form's undo verbs; without them the lines record no step. One user
   * intention is one step. A cell's change starts its group, named by the cell's form
   * path (`lines.2.label`), before it writes. A row patch a widget sends is written
   * inside its cell's group and ends it: a patch sent after its cell's change in the
   * same interaction joins that change's step; one sent first, or alone, is a step of
   * its own, and a change sent after it starts another. Adding, duplicating, removing
   * or reordering a line is one step through `perform`. Since a cell's path names its
   * row by position, the editor ends the cell groups it started when their control
   * commits, when a row patch for the cell is written, when the editor unmounts, before
   * its own row operations, and right after the rows change under it otherwise (a row
   * operation other code performs, an undo, a redo, a discard). After a row operation by
   * other code the steps are still one per intention and the interaction callbacks stay
   * paired, but that late commit's path names whichever row then sits at that position,
   * if any. A cell changed on after an undo starts a step of its own. The history
   * itself records a group's step when another field starts. A line added by code is
   * built with `emptyLineRow` or `duplicateLineRow` and then performed; while a cell's
   * group is open that is still one step, with the cell's late commit as above. Give the
   * editor and the history the same read-only state: a read-only history runs no
   * `perform`, so the row controls would do nothing while the cells still write. A
   * record form passes its interaction callbacks as `start` and `commit`, so they see a
   * cell as they see any other field.
   */
  history?: Pick<FormHistory, "perform" | "start" | "commit">;
}

/** Authored presentation of one line field, keyed by its name. */
export interface EditableLineField extends Pick<
  FieldDescriptor,
  "name" | "currencyField" | "description" | "options" | "relationEdit" | "statusDisplay" | "widget"
> {
  /** Column header and the cells' accessible name. */
  label?: string;
  /**
   * Locks this field's cell on the rows the domain says carry no such fact (a
   * section row has no product or price). The row stays editable elsewhere and
   * keeps its row actions; the backend owns the rule, this only presents it.
   */
  readOnlyWhen?: (row: Row, parentRow: Row | null) => boolean;
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
 * @kind surface
 */
export function EditableLines({
  control, setValue, name, lines, parentRow, readOnly, footer, rowErrors,
  primaryFields, fields: authoredFields = [], supplementalColumns = [], relationFilters, history,
}: EditableLinesProps): React.ReactElement {
  const t = useUiT();
  const controlId = React.useId();
  const config = React.useMemo(() => lineDiffConfig(lines), [lines]);
  const schemaMetadata = useSchemaFieldMetadata();
  // Keep RHF's presentation identity off the child's own public id.
  const { fields, append, insert, move, remove } = useFieldArray({ control, name, keyName: "rhfKey" });
  const { isDirty: formIsDirty } = useFormState({ control });
  const rows = (useWatch({ control, name }) as Row[] | undefined) ?? [];
  // Async widgets resolve their RHF identity at completion, never a captured index.
  const latest = React.useRef({ fields, readOnly, setValue, history });
  latest.current = { fields, readOnly, setValue, history };
  const mounted = React.useRef(true);
  React.useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);
  // The cell groups this editor started and has not seen committed, by form path, held
  // only to end them. A path names its row by position: the editor ends them before it
  // moves rows itself, and right after anything else has moved them, when the path may
  // already name another row. Committing a path whose group the history already ended
  // (another field started) records nothing, and one the form already ended (a discard,
  // a refresh, a save) does nothing at all.
  const openCells = React.useRef(new Set<string>());
  const endCell = React.useCallback((path: string) => {
    openCells.current.delete(path);
    latest.current.history?.commit(path);
  }, []);
  const endOpenCells = React.useCallback(() => openCells.current.forEach(endCell), [endCell]);
  // End them right after the rows changed under those paths by anything but this editor's
  // own row operations (which end them first, below): a row operation other code
  // performed, or a form reset (an undo, a redo, a discard), which gives every row a new
  // key. After another's row operation the steps stay one per intention and the commit
  // still pairs its start, but its path names whichever row now sits at that position, if
  // any. A cell changed on after an undo then starts a group of its own. Declared before
  // the fallback below, so a key that fallback writes joins no cell group.
  const rowKeys = fields.map((field) => field.rhfKey).join(" ");
  React.useEffect(() => { endOpenCells(); }, [endOpenCells, rowKeys]);
  // And when the editor unmounts (a pane that hides the lines, a closed record), so no start
  // is left without its commit. Only a cell's change records a path, after mount, so the
  // mount-time cleanup React's StrictMode runs finds none.
  React.useEffect(() => endOpenCells, [endOpenCells]);
  // The fallback for a row that arrives with no identity (a create form's seed rows, a
  // hand-built row): its client key is RHF's field-array key.
  React.useEffect(() => {
    fields.forEach((field, index) => {
      const row = rows[index];
      if (row && row[config.idField] == null && row[CLIENT_LINE_KEY] == null) {
        setValue<string>(`${name}.${index}.${CLIENT_LINE_KEY}`, field.rhfKey, { shouldDirty: false });
      }
    });
  }, [config.idField, fields, name, rows, setValue]);
  // One user intention is one step of the form's history, when the form has one. End the
  // open cell groups first, while each path still names its row.
  const performRowOperation = React.useCallback((action: () => void) => {
    const recorder = latest.current.history;
    if (!recorder) { action(); return; }
    endOpenCells();
    recorder.perform(action);
  }, [endOpenCells]);
  // Written inside its cell's group: joined to a change of the cell sent before it, or a
  // step of its own, and the group ends either way.
  const patchRow = React.useCallback((key: string, cell: string, patch: Record<string, unknown>) => {
    const current = latest.current;
    if (!mounted.current || current.readOnly) return;
    const index = current.fields.findIndex((field) => field.rhfKey === key);
    if (index < 0) return;
    const path = `${name}.${index}.${cell}`;
    current.history?.start(path);
    for (const [field, value] of Object.entries(patch)) {
      current.setValue<string>(`${name}.${index}.${field}`, value, { shouldDirty: true });
    }
    endCell(path);
  }, [endCell, name]);
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
    const currencyField = authored?.currencyField ?? field.currencyField;
    const statusDisplay = authored?.statusDisplay ?? field.statusDisplay;
    const descriptor: FieldDescriptor = {
      name: field.name, label: header, widget, options: authored?.options ?? enumOptions(field),
      ...(currencyField ? { currencyField } : {}),
      ...(statusDisplay ? { statusDisplay } : {}),
      ...(authored?.relationEdit === null ? { relationEdit: null } : {}),
    };
    const hasErrors = rowErrors?.some((error) => rowMessages(error, field.name).length > 0);
    return {
      id: field.name, field: `value.${field.name}`, header, widget,
      ...(currencyField ? { currencyField } : {}),
      ...(statusDisplay ? { statusDisplay } : {}),
      sortable: false, interactive: true,
      hiddenByDefault: Boolean(primary && !primary.has(field.name)),
      // Validation reveals an optional field even if the user previously hid it.
      hideable: !hasErrors,
      align: filterFieldType(field.name, field) === "number"
        || ["money", "integer", "float"].includes(widget ?? "") ? "right" : "left",
      render: ({ id, index, value }) => {
        const messages = rowMessages(rowErrors?.[index], field.name);
        const cellReadOnly = readOnly || lineLockedFields(value, config).includes(field.name)
          || Boolean(authored?.readOnlyWhen?.(value, parentRow ?? null));
        const controlProps: WidgetControlProps = {
          presentation: "cell", id: `${controlId}-${id}-${field.name}`,
          "aria-invalid": messages.length > 0 || undefined,
        };
        const path = `${name}.${index}.${field.name}`;
        return <div className="min-w-0">
          <Controller control={control} name={path}
            render={({ field: controller }) => {
              const onChange = (next: unknown) => {
                if (history) { history.start(path); openCells.current.add(path); }
                controller.onChange(next);
              };
              const onCommit = history && (() => endCell(path));
              return relationMulti ? (
                <RelationMultiFieldWidget
                  controlProps={controlProps}
                  value={Array.isArray(controller.value) ? controller.value : []}
                  onChange={onChange} onCommit={onCommit} readOnly={cellReadOnly} relation={relationMulti}
                  filters={relationFilters?.(field.name, parentRow ?? null)}
                  aria-label={header}
                />
              ) : relation ? (
                <RelationFieldWidget
                  controlProps={controlProps} controlRef={controller.ref}
                  value={relationValueId(controller.value) || null}
                  onChange={onChange} onCommit={onCommit} readOnly={cellReadOnly} relation={relation}
                  filters={relationFilters?.(field.name, parentRow ?? null)}
                  selectedOption={relationSelectedOption(controller.value, relation.labelField)}
                  edit={authored?.relationEdit}
                  aria-label={header}
                />
              ) : (
                <FieldDescriptorControl
                  controlProps={controlProps} controlRef={controller.ref} field={descriptor}
                  row={value} parentRow={parentRow} value={controller.value}
                  messages={messages} readOnly={cellReadOnly} onChange={onChange} onCommit={onCommit}
                  onRowChange={(patch) => patchRow(id, field.name, patch)}
                />
              );
            }}
          />
          {messages.map((message, index) => (
            <p key={index} className="mt-1 text-12 text-danger-text">{message}</p>
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
        <Chip tone="neutral" title={t("lines.systemHint")}>{t("lines.system")}</Chip>
      ),
    });
  }
  const rowActions = readOnly ? undefined : [
    defineRowAction<LineViewRow>({
      kind: "page", id: "duplicate", label: t("lines.duplicate"), icon: "copy",
      presentation: "icon", variant: "ghost", pendingPolicy: "disable-actions", visible: unlocked,
      onSelect: ({ index }) => performRowOperation(() => insert(index + 1, duplicateLineRow(rows[index] ?? {}, config) as never)),
    }),
    defineRowAction<LineViewRow>({
      kind: "page", id: "remove", label: t("lines.remove"), icon: "trash",
      presentation: "icon", variant: "danger", pendingPolicy: "disable-actions", visible: unlocked,
      onSelect: ({ index }) => performRowOperation(() => remove(index)),
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
        if (from >= 0 && to >= 0 && from !== to) performRowOperation(() => move(from, to));
      }}
      canReorderRow={unlocked}
      footerRow={readOnly ? undefined : <Button type="button" variant="ghost" size="sm"
        onClick={() => performRowOperation(() => append(emptyLineRow(fields.length, config) as never))}>
        <Glyph name="plus" decorative />{t("lines.add")}
      </Button>}
    />
    {described.length > 0 ? (
      <dl className="grid gap-1 px-2 pt-2 text-12 text-fg-muted">
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
