import * as React from "react";
import { useForm, useStore } from "@tanstack/react-form";
import { useBlocker } from "@tanstack/react-router";
import {
  useResourceMutation,
  useResourceRecord,
  type Row,
} from "@angee/sdk";

import { Button } from "../ui/button";
import { ErrorBanner } from "../fragments/ErrorBanner";
import { useConfirm } from "../feedback";
import {
  FieldDescription,
  FieldError,
  FieldLabel,
  FieldRoot,
} from "../ui/field";
import { FormActions } from "../ui/form-layout";
import { Input } from "../ui/input";
import { Spinner } from "../ui/spinner";
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
  className?: string;
}

type Values = Record<string, unknown>;

const TITLE_TEXT_CLASS =
  "block w-full min-w-0 truncate text-28 font-semibold leading-9 text-fg";
const TITLE_INPUT_CLASS =
  "h-auto min-h-9 rounded-none border-0 bg-transparent px-0 py-0 shadow-none " +
  "text-28 font-semibold leading-9 hover:border-transparent focus:border-transparent " +
  "focus:bg-transparent focus-visible:border-transparent placeholder:text-fg-subtle";

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
  const defaultValues = React.useMemo(
    () => emptyDraft(resolvedFields),
    [resolvedFields],
  );
  const formReadOnly = React.useMemo(
    () => resolvedFields.length > 0 && resolvedFields.every((field) => field.readOnly),
    [resolvedFields],
  );
  const baselineValuesRef = React.useRef<Values>(defaultValues);
  const [saveError, setSaveError] = React.useState<string | null>(null);
  const form = useForm({
    defaultValues,
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
        baselineValuesRef.current = defaultValues;
        form.reset(defaultValues);
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
  }, [defaultValues, isCreate, record, resolvedFields, form]);

  const titleField = titleFieldFor(resolvedFields);
  const statusField = resolvedFields.find((field) => field.widget === "statusbar");
  const bodyFields = React.useMemo(
    () =>
      resolvedFields.filter(
        (field) =>
          field.name !== statusField?.name && field.name !== titleField?.name,
      ),
    [resolvedFields, statusField?.name, titleField?.name],
  );
  const bodyGroups = React.useMemo(
    () =>
      resolvedGroups.map((group) => ({
        ...group,
        fields: group.fields.filter(
          (field) =>
            field.name !== statusField?.name && field.name !== titleField?.name,
        ),
      })),
    [resolvedGroups, statusField?.name, titleField?.name],
  );
  const sections = React.useMemo(
    () => formSections(bodyFields, bodyGroups),
    [bodyFields, bodyGroups],
  );

  return (
    <form
      className={cn("mx-auto flex w-full max-w-5xl flex-col gap-6", className)}
      onSubmit={(event) => {
        event.preventDefault();
        void form.handleSubmit();
      }}
    >
      <header className="grid gap-3">
        <div className="grid gap-4 md:grid-cols-[minmax(0,1fr)_auto]">
          <div className="min-w-0 self-start">
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
              <h2 className="truncate text-28 font-semibold leading-9 text-fg">
                Record
              </h2>
            )}
            {loading ? (
              <div className="mt-1 flex items-center gap-2 text-13 text-fg-muted">
                <Spinner size="sm" />
                Loading...
              </div>
            ) : null}
          </div>
          {statusField || headerActions ? (
            <div className="flex min-w-0 flex-col items-end gap-2">
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
              {headerActions ? (
                <div className="flex flex-wrap items-center justify-end gap-3">
                  {headerActions}
                </div>
              ) : null}
            </div>
          ) : null}
        </div>

        <form.Subscribe
          selector={(state) => ({
            canSubmit: state.canSubmit,
            isDirty: state.isDirty,
            isSubmitting: state.isSubmitting,
          })}
        >
          {(state) => {
            if (!isCreate && !state.isDirty) return null;
            const isSaving = mutation.fetching || state.isSubmitting;
            return (
              <div className="flex min-h-btn-md items-center border-y border-border-subtle py-2">
                <FormActions align="start" density="compact">
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
                    type="submit"
                    variant="primary"
                    size="sm"
                    loading={isSaving}
                    disabled={!state.canSubmit}
                  >
                    {submitLabel ?? (isCreate ? "Create" : "Save")}
                  </Button>
                </FormActions>
              </div>
            );
          }}
        </form.Subscribe>
      </header>

      <ErrorBanner message={saveError} title="Save failed" />

      {sections.map((section) => (
        <section
          key={section.key}
          className="grid gap-3"
        >
          {section.label ? (
            <h3 className="text-sm font-semibold text-fg">{section.label}</h3>
          ) : null}
          <div
            className={
              section.columns === 2
                ? "grid gap-4 md:grid-cols-2"
                : "grid gap-4"
            }
          >
            {section.fields.map((field) => (
              <form.Field key={field.name} name={field.name}>
                {(api) => {
                  const errors = api.state.meta.errors;
                  return (
                    <FieldRoot>
                      <FieldLabel>{field.label ?? field.name}</FieldLabel>
                      <FieldWidget
                        field={field}
                        value={api.state.value}
                        readOnly={field.readOnly}
                        onChange={(next) => api.handleChange(next)}
                      />
                      {field.description ? (
                        <FieldDescription>{field.description}</FieldDescription>
                      ) : null}
                      {errors.length > 0 ? (
                        <FieldError>{errors.join(", ")}</FieldError>
                      ) : null}
                    </FieldRoot>
                  );
                }}
              </form.Field>
            ))}
          </div>
        </section>
      ))}
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

type FormSection = {
  key: string;
  label?: React.ReactNode;
  columns?: number;
  fields: readonly FieldDescriptor[];
};

function formSections(
  fields: readonly FieldDescriptor[],
  groups: readonly GroupDescriptor[],
): readonly FormSection[] {
  if (groups.length === 0) return [{ key: "fields", fields }];
  const groupedNames = new Set<string>();
  const sections: FormSection[] = groups.flatMap((group, index) => {
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

function fallbackWidget(): WidgetDefinition {
  return {
    read: ({ value }) => <span className="text-13 text-fg">{String(value ?? "")}</span>,
  };
}
