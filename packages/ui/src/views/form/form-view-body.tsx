import * as React from "react";
import { Controller, useWatch, type Control } from "react-hook-form";

// Render-only bindings for the headless FormView surface.

import { Input } from "../../ui/input";
import {
  FieldDescription,
  FieldLabel,
  FieldRoot,
} from "../../ui/field";
import { FormGrid } from "../../ui/form-layout";
import { SectionEyebrow } from "../../ui/section-eyebrow";
import { Spinner } from "../../ui/spinner";
import { Tabs } from "../../ui/tabs";
import { textRoleVariants } from "../../ui/text";
import { cn } from "../../lib/cn";
import { relationValueId } from "../../widgets/types";
import type { RelationOption } from "../../widgets/RelationField";
import { EditableLines } from "./EditableLines";
import { FieldDescriptorControl } from "./field-descriptor-control";
import type { FieldDescriptor } from "../page";
import type { RelationFieldInfo } from "../model-metadata-defaults";
import { RelationFieldWidget } from "../relation/RelationFieldWidget";
import { relationSelectedOption } from "../relation/relation-options";
import {
  fieldAriaLabel,
  fieldErrorMessages,
  fieldValidationRules,
  gridFieldClass,
  recordRepresentationValue,
  titleText,
  visibleSections,
  type FormSectionModel,
  type FormValues,
} from "./form-view-model";
import type { FormViewSurface } from "./form-view-surface";

const TITLE_TEXT_CLASS =
  "block w-full min-w-0 truncate text-28 font-semibold leading-9 text-fg";
const TITLE_INPUT_CLASS =
  "h-auto min-h-9 rounded-none border-0 bg-transparent px-0 py-0 shadow-none " +
  "text-28 font-semibold leading-9 hover:border-transparent focus:border-transparent " +
  "focus:bg-transparent focus-visible:border-transparent placeholder:text-fg-subtle";
const EDITABLE_FIELD_CONTROL_CLASS = cn(
  "-mx-2 min-h-8 rounded-6 border border-transparent bg-transparent px-2",
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

/** Shared centered body width for the form and its saved-record panels. */
export const FORM_VIEW_COLUMN_CLASS =
  "mx-auto w-full max-w-[1100px] px-6 sm:px-8";

export function FormViewRecordHeader({
  surface,
}: {
  surface: FormViewSurface;
}): React.ReactElement {
  const {
    t,
    form,
    titleField,
    titleFieldMessages,
    displayRecord,
    modelMetadata,
    loading,
    subtitleParts,
    statusField,
    requiredFieldNames,
    requiredMessage,
    fieldReadOnly,
    clearServerFieldError,
    afterFieldChange,
  } = surface;
  return (
    <header className="grid gap-4">
      <div className="flex items-start gap-4 max-[900px]:flex-col max-[900px]:items-stretch">
        <div className="min-w-0 flex-1 self-start">
          {titleField ? (
            <Controller
              control={form.control}
              name={titleField.name}
              rules={fieldValidationRules(
                titleField,
                requiredFieldNames,
                requiredMessage,
              )}
              render={({ field: controller }) =>
                fieldReadOnly(titleField) ? (
                  <h1 className={TITLE_TEXT_CLASS}>
                    {titleText(controller.value, t("form.untitled"))}
                  </h1>
                ) : (
                  <Input
                    value={String(controller.value ?? "")}
                    placeholder={titleField.placeholder ?? t("form.untitled")}
                    aria-label={fieldAriaLabel(titleField)}
                    className={cn(TITLE_TEXT_CLASS, TITLE_INPUT_CLASS)}
                    onChange={(event) => {
                      clearServerFieldError(titleField.name);
                      controller.onChange(event.currentTarget.value);
                      afterFieldChange(titleField, event.currentTarget.value);
                    }}
                  />
                )
              }
            />
          ) : (
            <h1 className="truncate text-28 font-semibold leading-9 text-fg">
              {titleText(
                recordRepresentationValue(displayRecord, modelMetadata),
                t("form.record"),
              )}
            </h1>
          )}
          {titleField && titleFieldMessages.length > 0 ? (
            <p className="mt-1 text-xs leading-5 text-danger-text">
              {titleFieldMessages.join(", ")}
            </p>
          ) : null}
          <RecordSubtitle
            loading={loading}
            loadingLabel={t("form.loading")}
            parts={subtitleParts}
          />
        </div>
        {statusField ? (
          <div className="flex min-w-0 shrink-0 flex-wrap items-center justify-end gap-3 max-[900px]:w-full">
            <Controller
              control={form.control}
              name={statusField.name}
              rules={fieldValidationRules(
                statusField,
                requiredFieldNames,
                requiredMessage,
              )}
              render={({ field: controller }) => (
                <FieldDescriptorControl
                  field={statusField}
                  value={controller.value}
                  readOnly={fieldReadOnly(statusField)}
                  onChange={(next) => {
                    controller.onChange(next);
                    afterFieldChange(statusField, next);
                  }}
                />
              )}
            />
          </div>
        ) : null}
      </div>
    </header>
  );
}

export function FormViewOverview({
  surface,
  layout,
}: {
  surface: FormViewSurface;
  layout: "stacked" | "tabs";
}): React.ReactElement {
  const {
    t,
    form,
    hasConditionalFields,
    sections,
    linesActive,
    linesResource,
    linesField,
    formReadOnly,
    lineRowErrors,
    bodyField,
    requiredFieldNames,
    requiredMessage,
    serverFieldErrors,
    clearServerFieldError,
    afterFieldChange,
    fieldReadOnly,
  } = surface;
  const renderField = (field: FieldDescriptor): React.ReactNode => {
    const relation = surface.relationByField.get(field.name);
    const selectedOption = relation
      ? relationSelectedOption(
          surface.displayRecord?.[field.name],
          relation.labelField,
        )
      : undefined;
    return (
      <Controller
        key={field.name}
        control={form.control}
        name={field.name}
        rules={fieldValidationRules(field, requiredFieldNames, requiredMessage)}
        render={({ field: controller, fieldState }) => (
          <BoundFieldRow
            field={field}
            relation={relation}
            selectedOption={selectedOption}
            value={controller.value}
            readOnly={fieldReadOnly(field)}
            errors={fieldState.error ? [fieldState.error] : []}
            serverMessages={serverFieldErrors[field.name]}
            onChange={(next) => {
              clearServerFieldError(field.name);
              controller.onChange(next);
              afterFieldChange(field, next);
            }}
          />
        )}
      />
    );
  };
  const renderSections = (list: readonly FormSectionModel[]): React.ReactNode => {
    if (layout !== "tabs") {
      return list.map((section) => (
        <FormSection key={section.key} section={section} renderField={renderField} />
      ));
    }
    const stacked = list.filter((section) => section.label == null);
    const tabbedSections = list.filter(
      (section) => section.label != null && section.fields.length > 0,
    );
    return (
      <>
        {stacked.map((section) => (
          <FormSection key={section.key} section={section} renderField={renderField} />
        ))}
        {tabbedSections.length > 0 ? (
          <FormSectionTabs sections={tabbedSections} renderField={renderField} />
        ) : null}
      </>
    );
  };

  return (
    <>
      <div className="grid gap-6">
        {hasConditionalFields ? (
          <ConditionalSections
            control={form.control}
            sections={sections}
            renderSections={renderSections}
          />
        ) : (
          renderSections(sections)
        )}
      </div>
      {linesActive && linesResource && linesField ? (
        <section className="grid gap-3">
          <SectionEyebrow
            as="h3"
            spacing="field"
            tracking="wide"
            weight="semibold"
            className="border-b border-border-subtle pb-1"
          >
            {t("lines.section")}
          </SectionEyebrow>
          <EditableLines
            control={form.control}
            name={linesField}
            lines={linesResource}
            readOnly={formReadOnly}
            rowErrors={lineRowErrors}
          />
        </section>
      ) : null}
      {bodyField ? (
        <section className="grid gap-2">
          {bodyField.label ? (
            <SectionEyebrow as="span">{bodyField.label}</SectionEyebrow>
          ) : null}
          <Controller
            control={form.control}
            name={bodyField.name}
            rules={fieldValidationRules(
              bodyField,
              requiredFieldNames,
              requiredMessage,
            )}
            render={({ field: controller, fieldState }) => (
              <BodyFieldControl
                field={bodyField}
                value={controller.value}
                readOnly={fieldReadOnly(bodyField)}
                errors={fieldState.error ? [fieldState.error] : []}
                serverMessages={serverFieldErrors[bodyField.name]}
                onChange={(next) => {
                  clearServerFieldError(bodyField.name);
                  controller.onChange(next);
                  afterFieldChange(bodyField, next);
                }}
              />
            )}
          />
        </section>
      ) : null}
    </>
  );
}

function RecordSubtitle({
  loading,
  loadingLabel,
  parts,
}: {
  loading: boolean;
  loadingLabel: React.ReactNode;
  parts: readonly React.ReactNode[];
}): React.ReactElement | null {
  if (!loading && parts.length === 0) return null;
  return (
    <div
      className={cn(
        textRoleVariants({ role: "meta" }),
        "mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 font-mono",
      )}
    >
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
            {loadingLabel}
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
        <SectionEyebrow
          as="h3"
          spacing="field"
          tracking="wide"
          weight="semibold"
          className="border-b border-border-subtle pb-1"
        >
          {section.label}
        </SectionEyebrow>
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

function FormSectionTabs({
  sections,
  renderField,
}: {
  sections: readonly FormSectionModel[];
  renderField: (field: FieldDescriptor) => React.ReactNode;
}): React.ReactElement {
  const [active, setActive] = React.useState(sections[0]?.key);
  const value = sections.some((section) => section.key === active)
    ? active
    : sections[0]?.key;
  return (
    <Tabs value={value} onValueChange={setActive} variant="card">
      <Tabs.List>
        {sections.map((section) => (
          <Tabs.Tab key={section.key} value={section.key}>
            {section.label}
          </Tabs.Tab>
        ))}
      </Tabs.List>
      {sections.map((section) => (
        <Tabs.Panel key={section.key} value={section.key}>
          <FormSection
            section={{ ...section, label: undefined }}
            renderField={renderField}
          />
        </Tabs.Panel>
      ))}
    </Tabs>
  );
}

function ConditionalSections({
  control,
  sections,
  renderSections,
}: {
  control: Control<FormValues>;
  sections: readonly FormSectionModel[];
  renderSections: (list: readonly FormSectionModel[]) => React.ReactNode;
}): React.ReactNode {
  const values = useWatch({ control }) as FormValues;
  return renderSections(visibleSections(sections, values));
}

function BoundFieldRow({
  field,
  relation,
  selectedOption,
  value,
  readOnly,
  errors,
  serverMessages,
  onChange,
}: {
  field: FieldDescriptor;
  relation?: RelationFieldInfo;
  selectedOption?: RelationOption;
  value: unknown;
  readOnly?: boolean;
  errors: readonly unknown[];
  serverMessages?: readonly string[];
  onChange: (value: unknown) => void;
}): React.ReactElement {
  const effectiveReadOnly = Boolean(readOnly);
  const messages = [...fieldErrorMessages(errors), ...(serverMessages ?? [])];
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
          effectiveReadOnly
            ? READONLY_FIELD_CONTROL_CLASS
            : EDITABLE_FIELD_CONTROL_CLASS,
        )}
      >
        {relation ? (
          <RelationFieldWidget
            value={relationValueId(value) || null}
            onChange={onChange}
            readOnly={effectiveReadOnly}
            relation={relation}
            selectedOption={selectedOption}
            aria-label={fieldAriaLabel(field)}
          />
        ) : (
          <FieldDescriptorControl
            field={field}
            value={value}
            readOnly={effectiveReadOnly}
            onChange={onChange}
          />
        )}
      </div>
      <FieldFooter description={field.description} errors={messages} />
    </FieldRoot>
  );
}

function BodyFieldControl({
  field,
  value,
  readOnly,
  errors,
  serverMessages,
  onChange,
}: {
  field: FieldDescriptor;
  value: unknown;
  readOnly?: boolean;
  errors: readonly unknown[];
  serverMessages?: readonly string[];
  onChange: (value: unknown) => void;
}): React.ReactElement {
  const messages = [...fieldErrorMessages(errors), ...(serverMessages ?? [])];
  return (
    <FieldRoot invalid={messages.length > 0} className="grid gap-2">
      <FieldDescriptorControl
        field={field}
        value={value}
        readOnly={readOnly}
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
      {errors.length > 0 ? (
        <p className="text-xs leading-5 text-danger-text">{errors.join(", ")}</p>
      ) : null}
    </>
  );
}
