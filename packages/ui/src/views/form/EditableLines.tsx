import * as React from "react";
import {
  Controller,
  useFieldArray,
  useWatch,
  type Control,
  type FieldValues,
  type UseFormSetValue,
} from "react-hook-form";
import {
  DndContext,
  closestCenter,
  type DragEndEvent,
} from "@dnd-kit/core";
import {
  SortableContext,
  useSortable,
  verticalListSortingStrategy,
} from "@dnd-kit/sortable";
import {
  defaultWidgetForModelField,
  useSchemaFieldMetadata,
  type DataResourceLinesMetadata,
  type ModelFieldMetadata,
  type Row,
} from "@angee/metadata";

import { Glyph } from "../../chrome/Glyph";
import { useUiT, type UiTranslate } from "../../i18n";
import { cn } from "../../lib/cn";
import { useDndKitSensors } from "../../lib/dnd";
import { titleCase } from "../../lib/titleCase";
import { Button } from "../../ui/button";
import { relationValueId } from "../../widgets/types";
import {
  duplicateLineRow,
  emptyLineRow,
  lineDiffConfig,
  type LineDiffConfig,
} from "./editable-lines";
import { FieldDescriptorControl } from "./field-descriptor-control";
import {
  enumOptions,
  relationFieldInfoForField,
  relationListFieldInfoForField,
  type RelationFieldInfo,
} from "../resource/model-metadata-defaults";
import type { FieldDescriptor } from "../page";
import { RelationFieldWidget } from "../relation/RelationFieldWidget";
import { RelationMultiFieldWidget } from "../relation/RelationMultiFieldWidget";
import { relationSelectedOption } from "../relation/relation-options";
import type { ValidationErrors } from "./validation-errors";

export interface EditableLinesProps {
  /**
   * The composing form's react-hook-form control. `EditableLines` owns a
   * `useFieldArray` over `name`, so its rows join the form's values, dirty state,
   * and submit — the host reads `getValues(name)` at save time and diffs them
   * (`diffLines`) into the `<resource>_save` `lines` payload.
   */
  control: Control<Record<string, unknown>>;
  /**
   * React Hook Form's native leaf-value writer for widget-produced row patches.
   * Standalone callers pass `form.setValue` beside `form.control`; `FormView`
   * supplies both from its owned form automatically.
   */
  setValue: UseFormSetValue<Record<string, unknown>>;
  /** Form field holding the ordered child lines — the `linesResource.field`. */
  name: string;
  /** The resource's editable-lines contract (`modelMetadata.resource.linesResource`). */
  lines: DataResourceLinesMetadata;
  /** Owning document, passed to domain widgets without interpreting its fields. */
  parentRow?: Row | null;
  readOnly?: boolean;
  /**
   * Footer content (e.g. document totals) the composing form supplies. Receives the
   * live line rows so totals recompute as cells change; the composer owns the money
   * math, not this primitive.
   */
  footer?: (rows: readonly Row[]) => React.ReactNode;
  /** Server validation messages per line row, indexed by row position. */
  rowErrors?: readonly (ValidationErrors | undefined)[];
}

interface LineColumn {
  field: ModelFieldMetadata;
  descriptor: FieldDescriptor;
  relation: RelationFieldInfo | null;
  relationMulti: RelationFieldInfo | null;
  header: string;
}

const CELL_CLASS = "min-w-0";
const HANDLE_CLASS =
  "grid size-8 shrink-0 cursor-grab place-content-center rounded-6 text-fg-subtle " +
  "hover:bg-inset hover:text-fg touch-none select-none active:cursor-grabbing " +
  "focus-visible:focus-ring disabled:cursor-not-allowed disabled:opacity-40";

/**
 * The editable document-lines composer (F6): a drag-orderable list of child rows
 * bound to the parent form's `useFieldArray`, with each cell resolved from the child
 * resource's field metadata (a relation picker, a quantity/number input, or the money
 * widget by its registered key). Add / duplicate / remove maintain the set; drag
 * maintains order (the `position` column is derived from row order at save, not typed).
 * Server-computed fixed-N array values use `RowsField` instead; it has no
 * `useFieldArray` lifecycle or add/remove/reorder affordances.
 * Section/note pseudo-rows and matrix entry are deferred.
 */
export function EditableLines({
  control,
  setValue,
  name,
  lines,
  parentRow,
  readOnly,
  footer,
  rowErrors,
}: EditableLinesProps): React.ReactElement {
  const t = useUiT();
  const config = React.useMemo(() => lineDiffConfig(lines), [lines]);
  const schemaMetadata = useSchemaFieldMetadata();
  const columns = React.useMemo(
    () => lineColumns(lines, config, schemaMetadata),
    [lines, config, schemaMetadata],
  );
  // The array field lives on the parent form; a per-array keyName keeps rhf's row
  // key off the line's own `id` (which stays the public id used by the save diff).
  const { fields, append, insert, move, remove } = useFieldArray({
    control: control as unknown as Control<FieldValues>,
    name,
    keyName: "rhfKey",
  });
  const rows = (useWatch({
    control: control as unknown as Control<FieldValues>,
    name,
  }) as Row[] | undefined) ?? [];
  // Async widgets retain a callback after reorder/remove/refresh. Resolve its
  // RHF identity at completion, never write through the captured row index.
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
      current.setValue(`${name}.${index}.${field}` as never, value as never, { shouldDirty: true });
    }
  }, [name]);
  const sensors = useDndKitSensors(4);

  const onDragEnd = (event: DragEndEvent): void => {
    const { active, over } = event;
    if (readOnly || !over || active.id === over.id) return;
    const from = fields.findIndex((row) => row.rhfKey === active.id);
    const to = fields.findIndex((row) => row.rhfKey === over.id);
    if (from >= 0 && to >= 0) move(from, to);
  };

  // Header and rows reserve identical drag/action tracks. Minimum cell widths
  // belong to the grid; a narrow form scrolls this region rather than overlapping
  // neighboring controls. M2M chips get enough space for their selection summary.
  const widths = columns.map((column) => column.relationMulti ? 160 : 128);
  const gridStyle = {
    gridTemplateColumns: `32px ${widths.map((width) => `minmax(${width}px, 1fr)`).join(" ")} 68px`,
  };
  const minWidth = widths.reduce((total, width) => total + width, 100 + 18 + 8 * (columns.length + 1));

  return (
    <div className="min-w-0 overflow-x-auto">
      <div className="grid gap-2" style={{ minWidth }}>
        {fields.length > 0 ? (
          <div
            className="grid items-center gap-2 border border-transparent px-2 text-xs font-medium uppercase tracking-wide text-fg-muted"
            style={gridStyle}
            aria-hidden
          >
            <span />
            {columns.map((column) => (
              <span key={column.field.name} className="truncate">
                {column.header}
              </span>
            ))}
            <span />
          </div>
        ) : null}

        <DndContext
          sensors={sensors}
          collisionDetection={closestCenter}
          onDragEnd={onDragEnd}
        >
          <SortableContext
            items={fields.map((row) => row.rhfKey)}
            strategy={verticalListSortingStrategy}
          >
            <div className="grid gap-1">
              {fields.length === 0 ? (
                <p className="px-2 py-3 text-13 text-fg-muted">{t("lines.empty")}</p>
              ) : (
                fields.map((row, index) => (
                  <LineRow
                    key={row.rhfKey}
                    id={row.rhfKey}
                    index={index}
                    name={name}
                    control={control}
                    columns={columns}
                    row={rows[index]}
                    parentRow={parentRow}
                    onRowChange={(patch) => patchRow(row.rhfKey, patch)}
                    gridStyle={gridStyle}
                    readOnly={readOnly}
                    rowError={rowErrors?.[index]}
                    t={t}
                    onDuplicate={() =>
                      insert(index + 1, duplicateLineRow(rows[index] ?? {}, config) as never)
                    }
                    onRemove={() => remove(index)}
                  />
                ))
              )}
            </div>
          </SortableContext>
        </DndContext>

        {footer ? <div>{footer(rows)}</div> : null}

        {readOnly ? null : (
          <div>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => append(emptyLineRow(fields.length, config) as never)}
            >
              <Glyph name="plus" size={16} />
              {t("lines.add")}
            </Button>
          </div>
        )}
      </div>
    </div>
  );
}

function LineRow({
  id,
  index,
  name,
  control,
  columns,
  row,
  parentRow,
  onRowChange,
  gridStyle,
  readOnly,
  rowError,
  t,
  onDuplicate,
  onRemove,
}: {
  id: string;
  index: number;
  name: string;
  control: Control<Record<string, unknown>>;
  columns: readonly LineColumn[];
  row?: Row;
  parentRow?: Row | null;
  onRowChange: (patch: Record<string, unknown>) => void;
  gridStyle: React.CSSProperties;
  readOnly?: boolean;
  rowError?: ValidationErrors;
  t: UiTranslate;
  onDuplicate: () => void;
  onRemove: () => void;
}): React.ReactElement {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } =
    useSortable({ id });
  const { role: _dragRole, ...dragAttributes } = attributes;
  return (
    <div
      ref={setNodeRef}
      style={{ ...gridStyle, ...sortableTransformStyle(transform, transition) }}
      className={cn(
        "grid items-start gap-2 rounded-8 border border-transparent px-2 py-1.5",
        "hover:border-border-subtle hover:bg-inset/40",
        isDragging && "z-10 border-border-focus bg-sheet shadow-lg",
      )}
    >
      <button
        type="button"
        aria-label={t("lines.reorder")}
        className={HANDLE_CLASS}
        disabled={readOnly}
        {...(readOnly ? {} : dragAttributes)}
        {...(readOnly ? {} : listeners)}
      >
        <Glyph name="grip-vertical" size={16} />
      </button>

      {columns.map((column) => (
        <div key={column.field.name} className={CELL_CLASS}>
          <Controller
            control={control as unknown as Control<FieldValues>}
            name={`${name}.${index}.${column.field.name}`}
            render={({ field: controller }) =>
              column.relationMulti ? (
                <RelationMultiFieldWidget
                  compact
                  value={Array.isArray(controller.value) ? controller.value : []}
                  onChange={controller.onChange}
                  readOnly={readOnly}
                  relation={column.relationMulti}
                  aria-label={column.header}
                />
              ) : column.relation ? (
                <RelationFieldWidget
                  value={relationValueId(controller.value) || null}
                  onChange={controller.onChange}
                  readOnly={readOnly}
                  relation={column.relation}
                  selectedOption={relationSelectedOption(
                    controller.value,
                    column.relation.labelField,
                  )}
                  aria-label={column.header}
                />
              ) : (
                <FieldDescriptorControl
                  field={column.descriptor}
                  row={row}
                  parentRow={parentRow}
                  value={controller.value}
                  readOnly={readOnly}
                  onChange={controller.onChange}
                  onRowChange={onRowChange}
                />
              )
            }
          />
          {rowMessages(rowError, column.field.name).map((message, messageIndex) => (
            <p key={messageIndex} className="mt-1 text-xs text-danger-text">
              {message}
            </p>
          ))}
        </div>
      ))}

      {readOnly ? (
        <span />
      ) : (
        <div className="flex shrink-0 items-center gap-0.5">
          <button
            type="button"
            aria-label={t("lines.duplicate")}
            className={HANDLE_CLASS}
            onClick={onDuplicate}
          >
            <Glyph name="copy" size={15} />
          </button>
          <button
            type="button"
            aria-label={t("lines.remove")}
            className={HANDLE_CLASS}
            onClick={onRemove}
          >
            <Glyph name="trash" size={15} />
          </button>
        </div>
      )}
    </div>
  );
}

/** Resolve each editable child column: its widget descriptor and relation target. */
function lineColumns(
  lines: DataResourceLinesMetadata,
  config: LineDiffConfig,
  schemaMetadata: ReturnType<typeof useSchemaFieldMetadata>,
): LineColumn[] {
  return (lines.fields ?? [])
    .filter((field) => field.name !== config.positionField)
    .map((field) => {
      const widget = defaultWidgetForModelField(field);
      const customWidget = Boolean(field.widget && !["many2one", "many2many"].includes(field.widget));
      const options = enumOptions(field);
      const descriptor: FieldDescriptor = {
        name: field.name,
        ...(widget ? { widget } : {}),
        ...(options.length > 0 ? { options } : {}),
        ...(field.currencyField ? { currencyField: field.currencyField } : {}),
      };
      return {
        field,
        descriptor,
        relation: customWidget ? null : relationFieldInfoForField(field, schemaMetadata),
        relationMulti: customWidget ? null : relationListFieldInfoForField(field, schemaMetadata),
        header: titleCase(field.name),
      };
    });
}

function rowMessages(
  rowError: ValidationErrors | undefined,
  fieldName: string,
): readonly string[] {
  return rowError?.fieldErrors[fieldName] ?? [];
}

function sortableTransformStyle(
  transform: { x: number; y: number; scaleX: number; scaleY: number } | null,
  transition: string | undefined,
): React.CSSProperties {
  return {
    transform: transform
      ? `translate3d(${Math.round(transform.x)}px, ${Math.round(transform.y)}px, 0)`
      : undefined,
    transition,
  };
}
