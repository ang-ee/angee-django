import * as React from "react";
import { useForm } from "@tanstack/react-form";
import {
  useResourceMutation,
  useResourceRecord,
  type Row,
} from "@angee/sdk";

import { Button } from "../ui/button";
import {
  FieldDescription,
  FieldError,
  FieldLabel,
  FieldRoot,
} from "../ui/field";
import { Spinner } from "../ui/spinner";
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
  const form = useForm({
    defaultValues: emptyDraft(resolvedFields),
    onSubmit: async ({ value }) => {
      const data: Values = { ...value };
      if (!isCreate && id != null) data.id = id;
      const saved = await mutate({ data });
      if (saved) {
        form.reset(recordToValues(saved, resolvedFields));
        onSaved?.(saved);
      }
    },
  });

  const seededIdRef = React.useRef<string | null>(null);
  React.useEffect(() => {
    if (isCreate) {
      if (seededIdRef.current !== null) {
        seededIdRef.current = null;
        form.reset(emptyDraft(resolvedFields));
      }
      return;
    }
    const recordId = typeof record?.id === "string" ? record.id : null;
    if (record && recordId && seededIdRef.current !== recordId) {
      seededIdRef.current = recordId;
      form.reset(recordToValues(record, resolvedFields));
    }
  }, [isCreate, record, resolvedFields, form]);

  const titleField = resolvedFields.find((field) => field.title);
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
      className={["mx-auto flex w-full max-w-5xl flex-col gap-6", className]
        .filter(Boolean)
        .join(" ")}
      onSubmit={(event) => {
        event.preventDefault();
        void form.handleSubmit();
      }}
    >
      <div className="grid gap-4 md:grid-cols-[minmax(0,1fr)_auto]">
        <div className="min-w-0 self-start">
          {titleField ? (
            <form.Field name={titleField.name}>
              {(api) => (
                <input
                  value={String(api.state.value ?? "")}
                  placeholder="Untitled"
                  aria-label={fieldAriaLabel(titleField)}
                  readOnly={titleField.readOnly}
                  className="block w-full min-w-0 border-0 bg-transparent p-0 text-28 font-semibold leading-9 text-fg outline-none placeholder:text-fg-subtle focus-visible:focus-ring"
                  onChange={(event) =>
                    api.handleChange(event.currentTarget.value)
                  }
                />
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

      {mutation.error ? (
        <p className="text-13 text-danger-text">{mutation.error.message}</p>
      ) : null}

      <div className="flex items-center gap-2">
        <Button type="submit" variant="primary" loading={mutation.fetching}>
          {submitLabel ?? (isCreate ? "Create" : "Save")}
        </Button>
      </div>
    </form>
  );
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

function emptyValue(field: FieldDescriptor): unknown {
  if (field.widget === "tagInput") return [];
  if (field.kind === "switch" || field.widget === "switch") return false;
  return "";
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
