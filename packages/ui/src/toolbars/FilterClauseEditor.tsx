import * as React from "react";
import type { FilterOperator, FilterValue } from "@angee/metadata";
import { useUiT } from "../i18n";
import { cn } from "../lib/cn";
import { Button } from "../ui/button";
import { Input } from "../ui/input";
import { Select } from "../ui/select";
import { jsonValueFromUnknown } from "../widgets/json-value";
import { filterOperatorLabel } from "../views/resource/utils/labels";

export type FilterClauseFieldType = "text" | "number" | "date" | "datetime" | "selection" | "boolean";
export type FilterClauseOperator = FilterOperator | "isNotNull";
export interface FilterClauseChoice { value: string; label: React.ReactNode }
export interface FilterClauseField {
  id: string;
  field?: string;
  label: React.ReactNode;
  group?: string;
  type?: FilterClauseFieldType;
  options?: readonly FilterClauseChoice[];
  operators?: readonly FilterClauseOperator[];
  /** Declared picker; receives the same accessibility and read-only contract as native controls. */
  renderValue?: (props: {
    value: string;
    onValueChange: (value: string) => void;
    readOnly: boolean;
    "aria-label": string;
    "aria-describedby"?: string;
    "aria-invalid": boolean;
  }) => React.ReactNode;
}
export interface FilterClause {
  field: string;
  operator: FilterClauseOperator;
  value?: FilterValue;
  type?: FilterClauseFieldType;
}
/** Raw input stays controlled so incomplete numbers and JSON remain editable. */
export interface FilterClauseDraft { fieldId: string; operator: FilterClauseOperator; value: string }
export interface FilterClauseRowProps {
  fields: readonly FilterClauseField[];
  value: FilterClauseDraft;
  onChange: (value: FilterClauseDraft) => void;
  /** Enter in the row's native operand input; declared pickers own their keyboard events. */
  onSubmit?: () => void;
  error?: string;
  readOnly?: boolean;
  className?: string;
}

/** Shared field/operator/value controls; parents own clause identity and collection structure. */
export function FilterClauseRow({ fields, value, onChange, onSubmit, error, readOnly = false, className }: FilterClauseRowProps): React.ReactElement {
  const t = useUiT();
  const errorId = React.useId();
  const { field, operator } = resolveDraft(fields, value);
  const structured = structuredOperand(operator);
  const valueProps = {
    value: value.value,
    onValueChange: (next: string) => onChange({ ...value, fieldId: field?.id ?? "", operator, value: next }),
    readOnly,
    "aria-label": t("resourceToolbar.filterValue"),
    "aria-describedby": error ? errorId : undefined,
    "aria-invalid": Boolean(error),
  };
  return <div className={cn("grid min-w-0 gap-2", className)}>
    {field ? <>
      <Select size="sm" value={field.id} readOnly={readOnly}
        aria-label={t("resourceToolbar.filterField")}
        options={fields.map(({ id, label, group }) => ({ value: id, label, group }))}
        onValueChange={(id) => {
          const selected = fields.find((item) => item.id === id);
          if (selected) onChange({ fieldId: id, operator: defaultOperator(selected), value: "" });
        }} />
      <Select size="sm" value={operator} readOnly={readOnly}
        aria-label={t("resourceToolbar.filterOperator")}
        options={operatorsForField(field).map((item) => ({ value: item, label: filterOperatorLabel(item, t) }))}
        onValueChange={(next) => {
          const selected = operatorsForField(field).find((item) => item === next);
          if (selected) onChange({ ...value, fieldId: field.id, operator: selected });
        }} />
      {needsValue(operator) ? (
        field.renderValue && !structured ? field.renderValue(valueProps)
          : (field.options || field.type === "boolean") && !structured ? (
            <Select {...valueProps} size="sm" invalid={Boolean(error)} placeholder={t("resourceToolbar.value")}
              options={field.type === "boolean"
                ? [{ value: "true", label: t("list.yes") }, { value: "false", label: t("list.no") }]
                : field.options ?? []} />
          ) : (
            <Input size="sm" type={structured ? "text" : inputType(field)}
              value={valueProps.value} readOnly={readOnly}
              aria-label={valueProps["aria-label"]} aria-describedby={valueProps["aria-describedby"]}
              aria-invalid={valueProps["aria-invalid"]} placeholder={t("resourceToolbar.value")}
              onKeyDown={(event) => {
                if (!onSubmit || readOnly || event.key !== "Enter" || event.nativeEvent.isComposing
                  || event.target !== event.currentTarget || event.currentTarget.closest('[role="combobox"], [role="listbox"]')) return;
                event.preventDefault();
                event.stopPropagation();
                onSubmit();
              }}
              onChange={(event) => valueProps.onValueChange(event.currentTarget.value)} />
          )
      ) : null}
    </> : <p className="text-13 text-fg-muted">{t("resourceToolbar.noFilterFields")}</p>}
    {error ? <p id={errorId} role="alert" className="text-xs text-danger-text">{error}</p> : null}
  </div>;
}

export interface FilterClauseEditorProps {
  fields: readonly FilterClauseField[];
  onSubmit: (clause: FilterClause) => void;
  /** Optional controlled draft, for editing a clause in a parent-owned collection. */
  value?: FilterClauseDraft;
  onChange?: (value: FilterClauseDraft) => void;
  submitLabel?: React.ReactNode;
  readOnly?: boolean;
  className?: string;
}

/** Submit one typed clause. Uncontrolled mode clears the operand after adding;
 * controlled mode leaves reset, identity, and collection updates to the caller.
 * ResourceQuery remains responsible for executable field/operator validation. */
export function FilterClauseEditor({ fields, onSubmit, value, onChange, submitLabel, readOnly = false, className }: FilterClauseEditorProps): React.ReactElement {
  const t = useUiT();
  const [draft, setDraft] = React.useState<FilterClauseDraft>({ fieldId: "", operator: "contains", value: "" });
  const [failure, setFailure] = React.useState<{ draft: FilterClauseDraft; message: string }>();
  const editorRef = React.useRef<HTMLDivElement>(null);
  const current = value ?? draft;
  const { field, operator } = resolveDraft(fields, current);
  const error = failure?.draft.fieldId === current.fieldId
    && failure.draft.operator === current.operator && failure.draft.value === current.value
    ? failure.message : undefined;
  const change = (next: FilterClauseDraft) => {
    if (value === undefined) setDraft(next);
    setFailure(undefined);
    onChange?.(next);
  };
  const submit = () => {
    if (!field || readOnly) return;
    let operand: FilterValue | undefined;
    try {
      operand = needsValue(operator) ? coerceValue(field, current.value, operator) : undefined;
    } catch {
      setFailure({ draft: current, message: t("resourceToolbar.invalidJson") });
      return;
    }
    if (needsValue(operator) && operand === undefined) {
      setFailure({ draft: current, message: t("resourceToolbar.invalidValue") });
      return;
    }
    setFailure(undefined);
    onSubmit({ field: field.field ?? field.id, operator,
      ...(operand !== undefined ? { value: operand } : {}), ...(field.type ? { type: field.type } : {}) });
    if (value === undefined) change({ fieldId: field.id, operator, value: "" });
    globalThis.requestAnimationFrame(() => editorRef.current?.querySelector<HTMLElement>(
      "input:not([disabled]), button:not([disabled]), [tabindex='0']",
    )?.focus());
  };
  return <div ref={editorRef} className={cn("grid gap-2 rounded-6 border border-border-subtle bg-sheet p-2 shadow-xs", className)}>
    <FilterClauseRow fields={fields} value={current} onChange={change} onSubmit={submit} error={error} readOnly={readOnly} />
    {field && !readOnly ? <Button type="button" size="sm" variant="secondary" className="justify-center"
      disabled={needsValue(operator) && current.value.trim() === "" && !isDeclaredSelection(field, current.value, operator)} onClick={submit}>
      {submitLabel ?? t("resourceToolbar.add")}
    </Button> : null}
  </div>;
}

function operatorsForField(field: FilterClauseField): readonly FilterClauseOperator[] {
  return field.operators ?? ["exact"];
}
function defaultOperator(field: FilterClauseField): FilterClauseOperator {
  const operators = operatorsForField(field);
  const preferred: readonly FilterClauseOperator[] = field.type === "text" ? ["iContains", "contains", "exact"] : ["exact"];
  return preferred.find((operator) => operators.includes(operator)) ?? operators[0] ?? "exact";
}
function resolveDraft(fields: readonly FilterClauseField[], draft: FilterClauseDraft) {
  const field = fields.find((item) => item.id === draft.fieldId) ?? fields[0];
  const operator = field && !operatorsForField(field).includes(draft.operator) ? defaultOperator(field) : draft.operator;
  return { field, operator };
}
function needsValue(operator: FilterClauseOperator): boolean {
  return operator !== "isNull" && operator !== "isNotNull";
}
function structuredOperand(operator: FilterClauseOperator): boolean {
  return ["inList", "notInList", "hasKeysAny", "hasKeysAll", "jsonContains", "jsonContainedIn"].includes(operator);
}
function inputType(field: FilterClauseField): string {
  return field.type === "number" || field.type === "date" ? field.type : field.type === "datetime" ? "datetime-local" : "text";
}
function coerceValue(field: FilterClauseField, value: string, operator: FilterClauseOperator): FilterValue | undefined {
  if (isDeclaredSelection(field, value, operator)) return value;
  const trimmed = value.trim();
  if (!trimmed) return undefined;
  if (structuredOperand(operator)) return jsonValueFromUnknown(JSON.parse(trimmed));
  if (field.type === "number") {
    const number = Number(trimmed);
    return Number.isFinite(number) ? number : undefined;
  }
  if (field.type === "boolean") return trimmed === "true" ? true : trimmed === "false" ? false : undefined;
  return trimmed;
}

function isDeclaredSelection(field: FilterClauseField, value: string, operator: FilterClauseOperator): boolean {
  return !structuredOperand(operator) && field.type === "selection"
    && Boolean(field.options?.some((option) => option.value === value));
}
