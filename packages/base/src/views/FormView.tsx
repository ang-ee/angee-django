import * as React from "react";
import { useForm, useStore } from "@tanstack/react-form";
import { useBlocker } from "@tanstack/react-router";
import {
  useResourceMutation,
  useResourceRecord,
  type Row,
} from "@angee/sdk";

import { Button } from "../ui/button";
import { Glyph } from "../chrome/Glyph";
import { ErrorBanner } from "../fragments/ErrorBanner";
import { useConfirm } from "../feedback";
import {
  FieldDescription,
  FieldError,
  FieldLabel,
  FieldRoot,
} from "../ui/field";
import {
  FormGrid,
  FormSectionKicker,
} from "../ui/form-layout";
import { Input } from "../ui/input";
import { Spinner } from "../ui/spinner";
import { ControlBand } from "../shell/ControlBand";
import { cn } from "../lib/cn";
import {
  useResolvedWidget,
  type WidgetDefinition,
  type WidgetField,
} from "../widgets";
import {
  parsePageFields,
  parsePageGroups,
  type FieldDescriptor,
  type GroupDescriptor,
  type PageFieldKind,
} from "./page";

export type FieldKind = PageFieldKind;
export type FormField = FieldDescriptor;

export interface FormViewProps {
  model: string;
  id?: string | null;
  fields?: readonly FieldDescriptor[];
  groups?: readonly GroupDescriptor[];
  children?: React.ReactNode;
  returning?: readonly string[];
  onSaved?: (row: Row) => void;
  submitLabel?: React.ReactNode;
  headerActions?: React.ReactNode;
  /** Navigation chrome (record pager, view switcher) the host renders into the
   *  view toolbar alongside the dirty Save/Discard actions. */
  toolbar?: React.ReactNode;
  className?: string;
}

type Values = Record<string, unknown>;

const TITLE_TEXT_CLASS =
  "block w-full min-w-0 truncate text-28 font-semibold leading-9 text-fg";
const TITLE_INPUT_CLASS =
  "h-auto min-h-9 rounded-none border-0 bg-transparent px-0 py-0 shadow-none " +
  "text-28 font-semibold leading-9 hover:border-transparent focus:border-transparent " +
  "focus:bg-transparent focus-visible:border-transparent placeholder:text-fg-subtle";
// Interim: wrapper styling strips current widget chrome; widget recipes should own an appearance variant later.
const EDITABLE_FIELD_CONTROL_CLASS = cn(
  "-mx-2 min-h-8 rounded-md border border-transparent bg-transparent px-2",
  "transition-colors hover:border-border-subtle hover:bg-inset",
  "focus-within:border-border-focus focus-within:bg-sheet focus-within:focus-ring",
  "[&>button]:h-8 [&>button]:border-0 [&>button]:bg-transparent [&>button]:px-0 [&>button]:shadow-none",
  "[&>button:hover]:bg-transparent [&>button:focus-visible]:shadow-none",
  "[&>input]:h-8 [&>input]:border-0 [&>input]:bg-transparent [&>input]:px-0 [&>input]:shadow-none",
  "[&>input:focus]:border-transparent [&>input:focus]:shadow-none [&>input:focus-visible]:border-transparent [&>input:focus-visible]:shadow-none",
  "[&>textarea]:min-h-[120px] [&>textarea]:border-0 [&>textarea]:bg-transparent [&>textarea]:px-0 [&>textarea]:py-1.5 [&>textarea]:shadow-none",
  "[&>textarea:focus]:border-transparent [&>textarea:focus]:shadow-none",
  "[&>div]:border-0 [&>div]:bg-transparent [&>div]:shadow-none",
);
const READONLY_FIELD_CONTROL_CLASS = "min-h-8 text-13 text-fg";
const FIELD_ROOT_CLASS = "block min-w-0";
const FIELD_LABEL_CLASS =
  "mb-1 flex min-h-4 items-center justify-between gap-2 text-xs font-medium uppercase tracking-wide text-fg-muted";
const FIELD_CONTROL_CLASS = "min-w-0";
const FULL_FIELD_CLASS = "col-span-full";

export function FormView({
  model,
  id,
  fields,
  groups,
  children,
  returning,
  onSaved,
  submitLabel,
  headerActions,
  toolbar,
  className,
}: FormViewProps): React.ReactElement {
  const resolvedFields = React.useMemo(
    () => fields ?? parsePageFields(children),
    [children, fields],
  );
  const resolvedGroups = React.useMemo(
    () => groups ?? parsePageGroups(children),
    [children, groups],
  );
  const isCreate = id == null;
  const selection = React.useMemo(() => {
    const paths = new Set<string>(["id"]);
    for (const field of resolvedFields) paths.add(field.name);
    for (const extra of returning ?? []) paths.add(extra);
    return [...paths];
  }, [resolvedFields, returning]);

  const { record, fetching: loading } = useResourceRecord(model, id ?? null, {
    fields: selection,
    enabled: !isCreate,
  });
  const [mutate, mutation] = useResourceMutation(
    model,
    isCreate ? "create" : "update",
    { fields: selection },
  );
  const emptyValues = React.useMemo(
    () => emptyDraft(resolvedFields),
    [resolvedFields],
  );
  const formReadOnly = React.useMemo(
    () => resolvedFields.length > 0 && resolvedFields.every((field) => field.readOnly),
    [resolvedFields],
  );
  // `useForm` re-seeds an untouched form whenever `defaultValues` deep-changes.
  // Source it from this stable baseline ref (reassigned only on record seed,
  // post-save reset, and create reset) so a post-save re-render carrying new
  // field-descriptor identities can't re-seed and blank the just-saved values.
  const baselineValuesRef = React.useRef<Values>(emptyValues);
  const [saveError, setSaveError] = React.useState<string | null>(null);
  const form = useForm({
    defaultValues: baselineValuesRef.current,
    onSubmit: async ({ value }) => {
      setSaveError(null);
      const data = mutationData(value, resolvedFields, {
        baseline: baselineValuesRef.current,
        id,
        isCreate,
      });
      try {
        const saved = await mutate({ data });
        if (saved) {
          const savedValues = recordToValues(saved, resolvedFields);
          baselineValuesRef.current = savedValues;
          form.reset(savedValues);
          onSaved?.(saved);
        }
      } catch (error) {
        setSaveError(
          error instanceof Error ? error.message : "Could not save record.",
        );
      }
    },
  });
  const formIsDirty = useStore(form.store, (state) => state.isDirty);
  useUnsavedChangesNavigationGuard({
    isDirty: formIsDirty,
    readOnly: formReadOnly,
  });

  const seededIdRef = React.useRef<string | null>(null);
  React.useEffect(() => {
    setSaveError(null);
  }, [model, id]);

  React.useEffect(() => {
    if (isCreate) {
      if (seededIdRef.current !== null) {
        seededIdRef.current = null;
        baselineValuesRef.current = emptyValues;
        form.reset(emptyValues);
        setSaveError(null);
      }
      return;
    }
    const recordId = typeof record?.id === "string" ? record.id : null;
    if (record && recordId && seededIdRef.current !== recordId) {
      seededIdRef.current = recordId;
      const recordValues = recordToValues(record, resolvedFields);
      baselineValuesRef.current = recordValues;
      form.reset(recordValues);
      setSaveError(null);
    }
  }, [emptyValues, isCreate, record, resolvedFields, form]);

  const titleField = titleFieldFor(resolvedFields);
  const statusField = resolvedFields.find((field) => field.widget === "statusbar");
  const bodyField = React.useMemo(
    () => bodyFieldFor(resolvedFields, titleField, statusField),
    [resolvedFields, statusField, titleField],
  );
  const gridFields = React.useMemo(
    () =>
      resolvedFields.filter(
        (field) =>
          field.name !== bodyField?.name &&
          field.name !== statusField?.name &&
          field.name !== titleField?.name,
      ),
    [bodyField?.name, resolvedFields, statusField?.name, titleField?.name],
  );
  const gridGroups = React.useMemo(
    () =>
      resolvedGroups.map((group) => ({
        ...group,
        fields: group.fields.filter(
          (field) =>
            field.name !== bodyField?.name &&
            field.name !== statusField?.name && field.name !== titleField?.name,
        ),
      })),
    [bodyField?.name, resolvedGroups, statusField?.name, titleField?.name],
  );
  const sections = React.useMemo(
    () => formSections(gridFields, gridGroups),
    [gridFields, gridGroups],
  );
  const subtitleParts = React.useMemo(
    () => recordSubtitleParts(record, id),
    [id, record],
  );

  return (
    <form
      className={cn("min-h-full bg-sheet", className)}
      onSubmit={(event) => {
        event.preventDefault();
        void form.handleSubmit();
      }}
    >
      <form.Subscribe
        selector={(state) => ({
          canSubmit: state.canSubmit,
          isDirty: state.isDirty,
          isSubmitting: state.isSubmitting,
        })}
      >
        {(state) => {
          const showActions = isCreate || state.isDirty;
          if (!toolbar && !showActions) return null;
          const isSaving = mutation.fetching || state.isSubmitting;
          // Under a shell the band portals out of the <form>, so Save must
          // submit via handleSubmit() rather than relying on native type="submit".
          return (
            <ControlBand className={state.isDirty ? "bg-brand-soft" : undefined}>
              <div className="min-w-2 flex-1" />
              {toolbar ? (
                <div className="flex min-w-0 items-center gap-2">{toolbar}</div>
              ) : null}
              {showActions ? (
                <div className="flex items-center gap-2">
                  {state.isDirty ? (
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      disabled={isSaving}
                      onClick={() => form.reset()}
                    >
                      Discard
                    </Button>
                  ) : null}
                  <Button
                    type="button"
                    variant="primary"
                    size="sm"
                    loading={isSaving}
                    disabled={!state.canSubmit}
                    onClick={() => {
                      void form.handleSubmit();
                    }}
                  >
                    {submitLabel ?? (isCreate ? "Create" : "Save")}
                  </Button>
                </div>
              ) : null}
            </ControlBand>
          );
        }}
      </form.Subscribe>
      <div className="mx-auto flex w-full max-w-[1100px] flex-col gap-6 px-6 py-6 pb-12 sm:px-8">
        <header className="grid gap-4">
          <div className="flex items-start gap-4 max-[900px]:flex-col max-[900px]:items-stretch">
            <div className="min-w-0 flex-1 self-start">
              {titleField ? (
                <form.Field name={titleField.name}>
                  {(api) => (
                    titleField.readOnly ? (
                      <h1 className={TITLE_TEXT_CLASS}>
                        {titleText(api.state.value)}
                      </h1>
                    ) : (
                      <Input
                        value={String(api.state.value ?? "")}
                        placeholder={titleField.placeholder ?? "Untitled"}
                        aria-label={fieldAriaLabel(titleField)}
                        className={cn(TITLE_TEXT_CLASS, TITLE_INPUT_CLASS)}
                        onChange={(event) =>
                          api.handleChange(event.currentTarget.value)
                        }
                      />
                    )
                  )}
                </form.Field>
              ) : (
                <h1 className="truncate text-28 font-semibold leading-9 text-fg">
                  Record
                </h1>
              )}
              <RecordSubtitle loading={loading} parts={subtitleParts} />
            </div>
            <div className="flex min-w-0 shrink-0 flex-wrap items-center justify-end gap-3 max-[900px]:w-full">
              {statusField ? (
                <form.Field name={statusField.name}>
                  {(api) => (
                    <FieldWidget
                      field={statusField}
                      value={api.state.value}
                      readOnly={statusField.readOnly}
                      onChange={(next) => api.handleChange(next)}
                    />
                  )}
                </form.Field>
              ) : null}
              {/* Presentational pending record action wiring. */}
              <div className="flex items-center gap-1">
                <Button
                  type="button"
                  variant="icon"
                  size="iconMd"
                  aria-label="Star"
                  className="text-amber-500 hover:text-amber-500"
                >
                  <Glyph name="star" className="fill-current" />
                </Button>
                <Button
                  type="button"
                  variant="icon"
                  size="iconMd"
                  aria-label="Share"
                >
                  <Glyph name="share" />
                </Button>
              </div>
              {headerActions ? (
                <div className="flex flex-wrap items-center justify-end gap-3">
                  {headerActions}
                </div>
              ) : null}
            </div>
          </div>

        </header>

        <ErrorBanner message={saveError} title="Save failed" />

        <div className="grid gap-6">
          {sections.map((section) => (
            <FormSection
              key={section.key}
              section={section}
              renderField={(field) => (
                <form.Field key={field.name} name={field.name}>
                  {(api) => (
                    <BoundFieldRow
                      field={field}
                      value={api.state.value}
                      errors={api.state.meta.errors}
                      onChange={(next) => api.handleChange(next)}
                    />
                  )}
                </form.Field>
              )}
            />
          ))}
        </div>

        {bodyField ? (
          <section className="grid gap-2">
            {bodyField.label ? (
              <FormSectionKicker>{bodyField.label}</FormSectionKicker>
            ) : null}
            <form.Field name={bodyField.name}>
              {(api) => (
                <BodyFieldControl
                  field={bodyField}
                  value={api.state.value}
                  errors={api.state.meta.errors}
                  onChange={(next) => api.handleChange(next)}
                />
              )}
            </form.Field>
          </section>
        ) : null}
      </div>
    </form>
  );
}

function useUnsavedChangesNavigationGuard({
  isDirty,
  readOnly,
}: {
  isDirty: boolean;
  readOnly: boolean;
}): void {
  const confirm = useConfirm();
  const shouldBlockFn = React.useCallback(async () => {
    if (readOnly || !isDirty) return false;
    const leave = await confirm({
      title: "Unsaved changes — leave without saving?",
      cancel: "Stay",
      confirm: "Leave",
      danger: true,
    });
    return !leave;
  }, [confirm, isDirty, readOnly]);

  useBlocker({
    shouldBlockFn,
    enableBeforeUnload: isDirty && !readOnly,
    disabled: readOnly || !isDirty,
  });
}

function FieldWidget({
  field,
  value,
  readOnly,
  onChange,
}: {
  field: FieldDescriptor;
  value: unknown;
  readOnly?: boolean;
  onChange?: (value: unknown) => void;
}): React.ReactElement {
  const widget = useResolvedWidget(widgetId(field)) ?? fallbackWidget();
  const Component = readOnly ? widget.read : (widget.edit ?? widget.read);
  const widgetField: WidgetField = {
    name: field.name,
    label: field.label,
    options: field.options,
  };
  return (
    <Component
      value={value}
      field={widgetField}
      readOnly={readOnly}
      onChange={onChange}
    />
  );
}

type FormSectionModel = {
  key: string;
  label?: React.ReactNode;
  columns?: number;
  fields: readonly FieldDescriptor[];
};

function RecordSubtitle({
  loading,
  parts,
}: {
  loading: boolean;
  parts: readonly React.ReactNode[];
}): React.ReactElement | null {
  if (!loading && parts.length === 0) return null;
  return (
    <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 font-mono text-13 text-fg-muted">
      {parts.map((part, index) => (
        <React.Fragment key={index}>
          {index > 0 ? <span aria-hidden="true">/</span> : null}
          <span>{part}</span>
        </React.Fragment>
      ))}
      {loading ? (
        <>
          {parts.length > 0 ? <span aria-hidden="true">/</span> : null}
          <span className="inline-flex items-center gap-2">
            <Spinner size="sm" />
            Loading...
          </span>
        </>
      ) : null}
    </div>
  );
}

function FormSection({
  section,
  renderField,
}: {
  section: FormSectionModel;
  renderField: (field: FieldDescriptor) => React.ReactNode;
}): React.ReactElement | null {
  if (section.fields.length === 0) return null;
  return (
    <section className="grid gap-3">
      {section.label ? (
        <FormSectionKicker
          as="h3"
          spacing="field"
          tracking="wide"
          weight="semibold"
          className="border-b border-border-subtle pb-1"
        >
          {section.label}
        </FormSectionKicker>
      ) : null}
      <FormGrid
        columns={section.columns === 1 ? "one" : "two"}
        density="comfortable"
        className="gap-x-8 gap-y-4 pb-2"
      >
        {section.fields.map((field) => renderField(field))}
      </FormGrid>
    </section>
  );
}

function BoundFieldRow({
  field,
  value,
  errors,
  onChange,
}: {
  field: FieldDescriptor;
  value: unknown;
  errors: readonly unknown[];
  onChange: (value: unknown) => void;
}): React.ReactElement {
  const readOnly = Boolean(field.readOnly);
  const messages = fieldErrorMessages(errors);
  return (
    <FieldRoot
      invalid={messages.length > 0}
      className={cn(FIELD_ROOT_CLASS, gridFieldClass(field))}
    >
      <FieldLabel className={FIELD_LABEL_CLASS}>
        {field.label ?? field.name}
      </FieldLabel>
      <div
        className={cn(
          FIELD_CONTROL_CLASS,
          readOnly ? READONLY_FIELD_CONTROL_CLASS : EDITABLE_FIELD_CONTROL_CLASS,
        )}
      >
        <FieldWidget
          field={field}
          value={value}
          readOnly={field.readOnly}
          onChange={onChange}
        />
      </div>
      <FieldFooter description={field.description} errors={messages} />
    </FieldRoot>
  );
}

function BodyFieldControl({
  field,
  value,
  errors,
  onChange,
}: {
  field: FieldDescriptor;
  value: unknown;
  errors: readonly unknown[];
  onChange: (value: unknown) => void;
}): React.ReactElement {
  const messages = fieldErrorMessages(errors);
  return (
    <FieldRoot invalid={messages.length > 0} className="grid gap-2">
      <FieldWidget
        field={field}
        value={value}
        readOnly={field.readOnly}
        onChange={onChange}
      />
      <FieldFooter description={field.description} errors={messages} />
    </FieldRoot>
  );
}

function FieldFooter({
  description,
  errors,
}: {
  description?: React.ReactNode;
  errors: readonly string[];
}): React.ReactElement | null {
  if (!description && errors.length === 0) return null;
  return (
    <>
      {description ? <FieldDescription>{description}</FieldDescription> : null}
      {errors.length > 0 ? <FieldError>{errors.join(", ")}</FieldError> : null}
    </>
  );
}

function formSections(
  fields: readonly FieldDescriptor[],
  groups: readonly GroupDescriptor[],
): readonly FormSectionModel[] {
  if (groups.length === 0) return [{ key: "fields", fields }];
  const groupedNames = new Set<string>();
  const sections: FormSectionModel[] = groups.flatMap((group, index) => {
    if (group.fields.length === 0) return [];
    for (const field of group.fields) groupedNames.add(field.name);
    return [
      {
        key: `group:${index}:${String(group.label ?? "")}`,
        label: group.label,
        columns: group.columns,
        fields: group.fields,
      },
    ];
  });
  const ungrouped = fields.filter((field) => !groupedNames.has(field.name));
  if (ungrouped.length > 0) sections.unshift({ key: "fields", fields: ungrouped });
  return sections;
}

function titleFieldFor(
  fields: readonly FieldDescriptor[],
): FieldDescriptor | undefined {
  return fields.find((field) => field.title) ??
    fields.find((field) => field.name === "title");
}

function bodyFieldFor(
  fields: readonly FieldDescriptor[],
  titleField: FieldDescriptor | undefined,
  statusField: FieldDescriptor | undefined,
): FieldDescriptor | undefined {
  const candidates = fields.filter(
    (field) => field.name !== titleField?.name && field.name !== statusField?.name,
  );
  return candidates.find((field) => field.body) ??
    candidates.find(isNamedBodyField) ??
    candidates.find(isLongTextField);
}

function isNamedBodyField(field: FieldDescriptor): boolean {
  const name = normaliseFieldName(field.name);
  return name === "body" || name === "description";
}

function isLongTextField(field: FieldDescriptor): boolean {
  const id = widgetId(field);
  return (
    id === "textarea" ||
    id === "markdown" ||
    id === "markdown.editor" ||
    id === "markdown.preview" ||
    field.kind === "textarea"
  );
}

function titleText(value: unknown): string {
  const text = String(value ?? "").trim();
  return text || "Untitled";
}

function emptyDraft(fields: readonly FieldDescriptor[]): Values {
  const draft: Values = {};
  for (const field of fields) draft[field.name] = emptyValue(field);
  return draft;
}

function recordToValues(record: Row, fields: readonly FieldDescriptor[]): Values {
  const values: Values = {};
  for (const field of fields) {
    values[field.name] = record[field.name] ?? emptyValue(field);
  }
  return values;
}

function mutationData(
  values: Values,
  fields: readonly FieldDescriptor[],
  options: {
    baseline: Values;
    id?: string | null;
    isCreate: boolean;
  },
): Values {
  const data: Values = {};
  for (const field of fields) {
    if (field.readOnly) continue;
    const next = values[field.name];
    if (isUnselectedOption(field, next)) continue;
    if (!options.isCreate && valuesEqual(next, options.baseline[field.name])) {
      continue;
    }
    data[field.name] = next;
  }
  if (!options.isCreate && options.id != null) data.id = options.id;
  return data;
}

function emptyValue(field: FieldDescriptor): unknown {
  if (field.widget === "tagInput") return [];
  if (field.kind === "switch" || field.widget === "switch") return false;
  return "";
}

function hasOptionValue(field: FieldDescriptor): boolean {
  return Boolean(
    field.options &&
      (field.widget === "select" ||
        field.widget === "statusbar" ||
        field.kind === "select" ||
        field.kind === "selection"),
  );
}

function isUnselectedOption(field: FieldDescriptor, value: unknown): boolean {
  return value === "" && hasOptionValue(field);
}

function valuesEqual(left: unknown, right: unknown): boolean {
  if (Object.is(left, right)) return true;
  if (!Array.isArray(left) || !Array.isArray(right)) return false;
  return (
    left.length === right.length &&
    left.every((item, index) => valuesEqual(item, right[index]))
  );
}

function widgetId(field: FieldDescriptor): string {
  if (field.widget) return field.widget;
  return field.kind ?? "text";
}

function fieldAriaLabel(field: FieldDescriptor): string {
  return typeof field.label === "string" ? field.label : field.name;
}

function gridFieldClass(field: FieldDescriptor): string | undefined {
  return field.widget === "tagInput" ? FULL_FIELD_CLASS : undefined;
}

function fieldErrorMessages(errors: readonly unknown[]): string[] {
  return errors.map(fieldErrorMessage);
}

function fieldErrorMessage(error: unknown): string {
  if (typeof error === "string") return error;
  if (
    error &&
    typeof error === "object" &&
    "message" in error &&
    (typeof error.message === "string" || typeof error.message === "number")
  ) {
    return String(error.message);
  }
  return String(error);
}

function recordSubtitleParts(
  record: Row | null | undefined,
  id: string | null | undefined,
): React.ReactNode[] {
  const parts: React.ReactNode[] = [];
  const recordId = presentValue(record?.id) ?? presentValue(id);
  if (recordId !== undefined) parts.push(shortRecordId(String(recordId)));
  if (record) {
    // Reads conventional metadata names until Row exposes typed metadata.
    const created = recordValue(record, ["createdAt", "created_at", "created"]);
    const updated = recordValue(record, ["updatedAt", "updated_at", "updated"]);
    const words = recordValue(record, ["wordCount", "word_count", "words"]);
    if (created !== undefined) parts.push(`created ${formatRecordDate(created)}`);
    if (updated !== undefined) parts.push(`updated ${formatRecordDate(updated)}`);
    if (words !== undefined) parts.push(formatWordCount(words));
  }
  return parts.filter((part) => String(part).trim() !== "");
}

function recordValue(
  record: Row,
  names: readonly string[],
): unknown | undefined {
  for (const name of names) {
    const value = presentValue(record[name]);
    if (value !== undefined) return value;
  }
  const normalised = new Set(names.map(normaliseFieldName));
  for (const [key, value] of Object.entries(record)) {
    if (normalised.has(normaliseFieldName(key))) {
      const present = presentValue(value);
      if (present !== undefined) return present;
    }
  }
  return undefined;
}

function presentValue(value: unknown): unknown | undefined {
  if (value == null) return undefined;
  if (typeof value === "string" && value.trim() === "") return undefined;
  return value;
}

function shortRecordId(value: string): string {
  const text = value.trim();
  if (text.length <= 12) return text;
  return text.slice(0, 8);
}

function formatRecordDate(value: unknown): string {
  const text = String(value);
  const date = new Date(text);
  if (Number.isNaN(date.getTime())) return text;
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
  }).format(date);
}

function formatWordCount(value: unknown): string {
  const count =
    typeof value === "number"
      ? value
      : typeof value === "string"
        ? Number(value)
        : Number.NaN;
  if (Number.isFinite(count)) {
    return `${new Intl.NumberFormat().format(count)} words`;
  }
  return `${String(value)} words`;
}

function normaliseFieldName(value: string): string {
  return value.replace(/[-_\s]+/g, "").toLowerCase();
}

function fallbackWidget(): WidgetDefinition {
  return {
    read: ({ value }) => <span className="text-13 text-fg">{String(value ?? "")}</span>,
  };
}
