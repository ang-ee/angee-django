import * as React from "react";
import { Controller, useWatch, type Control } from "react-hook-form";

// Render-only bindings for the headless FormView surface.

import { Input } from "../../ui/input";
import { Badge } from "../../ui/badge";
import {
  FieldDescription,
  FieldLabel,
  FieldRoot,
} from "../../ui/field";
import { FormGrid } from "../../ui/form-layout";
import { SectionEyebrow } from "../../ui/section-eyebrow";
import { Spinner } from "../../ui/spinner";
import { Tabs } from "../../ui/tabs";
import { renderGlyph } from "../../chrome/Glyph";
import { textRoleVariants } from "../../ui/text";
import { cn } from "../../lib/cn";
import { optionLabel, relationValueId } from "../../widgets/types";
import { statusTone } from "../../widgets/status-tones";
import type { RelationOption } from "../../widgets/RelationField";
import { EditableLines } from "./EditableLines";
import { FieldDescriptorControl } from "./field-descriptor-control";
import { DescriptorPresenceControl } from "./descriptor-presence-control";
import type { FieldDescriptor } from "../page";
import type { RelationFieldInfo } from "../resource/model-metadata-defaults";
import { RelationFieldWidget } from "../relation/RelationFieldWidget";
import { relationSelectedOption } from "../relation/relation-options";
import {
  fieldAriaLabel,
  fieldErrorMessages,
  gridFieldClass,
  recordRepresentationValue,
  resolveField,
  titleText,
  visibleSections,
  type FormSectionModel,
  type FormValues,
} from "./form-view-model";
import type { FormViewSurface } from "./form-view-surface";
import { directDottedPathMessages } from "./validation-errors";

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
  compact = false,
}: {
  surface: FormViewSurface;
  compact?: boolean;
}): React.ReactElement {
  const {
    t,
    form,
    titleField,
    titleFieldMessages,
    displayRecord,
    modelMetadata,
    relationByField,
    loading,
    subtitleParts,
    statusField,
    fieldReadOnly,
    clearServerFieldError,
    afterFieldChange,
  } = surface;
  const headerValues = useWatch({
    control: form.control,
    disabled: !titleField?.resolve && !statusField?.resolve,
  }) as FormValues;
  const currentTitleField = titleField
    ? resolveField(titleField, headerValues)
    : undefined;
  const currentStatusField = statusField
    ? resolveField(statusField, headerValues)
    : undefined;
  const titleRelation = currentTitleField
    ? relationByField.get(currentTitleField.name)
    : undefined;
  const titleSelectedOption = currentTitleField && titleRelation
    ? relationSelectedOption(
        displayRecord?.[currentTitleField.name],
        titleRelation.labelField,
      )
    : undefined;
  return (
    <header className={cn("grid", compact ? "gap-1" : "gap-4")}>
      <div className="flex items-start gap-4 max-[900px]:flex-col max-[900px]:items-stretch">
        <div className="min-w-0 flex-1 self-start">
          {currentTitleField ? (
            <Controller
              control={form.control}
              name={currentTitleField.name}
              render={({ field: controller }) =>
                fieldReadOnly(currentTitleField) ? (
                  <h1 className={compact ? "truncate text-base font-semibold text-fg" : TITLE_TEXT_CLASS}>
                    {titleText(
                      titleRelation
                        ? titleSelectedOption?.label ?? relationValueId(controller.value)
                        : controller.value,
                      t("form.untitled"),
                    )}
                  </h1>
                ) : titleRelation ? (
                  <div className={compact ? "min-w-0 text-base font-semibold" : TITLE_TEXT_CLASS}>
                    <RelationFieldWidget
                      value={relationValueId(controller.value) || null}
                      onChange={(next) => {
                        clearServerFieldError(currentTitleField.name);
                        controller.onChange(next);
                        afterFieldChange(currentTitleField, next);
                      }}
                      relation={titleRelation}
                      selectedOption={titleSelectedOption}
                      placeholder={currentTitleField.placeholder ?? t("form.untitled")}
                      aria-label={fieldAriaLabel(currentTitleField)}
                    />
                  </div>
                ) : (
                  <Input
                    value={String(controller.value ?? "")}
                    placeholder={currentTitleField.placeholder ?? t("form.untitled")}
                    aria-label={fieldAriaLabel(currentTitleField)}
                    className={cn(
                      compact ? "h-8 border-0 bg-transparent px-0 text-base font-semibold shadow-none" : cn(TITLE_TEXT_CLASS, TITLE_INPUT_CLASS),
                    )}
                    onChange={(event) => {
                      clearServerFieldError(currentTitleField.name);
                      controller.onChange(event.currentTarget.value);
                      afterFieldChange(currentTitleField, event.currentTarget.value);
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
          {!compact ? <RecordSubtitle loading={loading} loadingLabel={t("form.loading")} parts={subtitleParts} /> : null}
        </div>
        {currentStatusField && compact ? (
          <Controller
            control={form.control}
            name={currentStatusField.name}
            render={({ field: controller }) => {
              const value = typeof controller.value === "string"
                ? controller.value
                : "";
              return value ? (
                <Badge
                  tone={statusTone(value)}
                  density="compact"
                  shape="pill"
                  className="self-start"
                >
                  {optionLabel(currentStatusField.options, value)}
                </Badge>
              ) : <span aria-hidden />;
            }}
          />
        ) : currentStatusField ? (
          <div className="flex min-w-0 shrink-0 flex-wrap items-center justify-end gap-3 max-[900px]:w-full">
            <Controller
              control={form.control}
              name={currentStatusField.name}
              render={({ field: controller }) => (
                <FieldDescriptorControl
                  field={currentStatusField}
                  value={controller.value}
                  readOnly={fieldReadOnly(currentStatusField)}
                  onChange={(next) => {
                    controller.onChange(next);
                    afterFieldChange(currentStatusField, next);
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
    clearServerFieldError,
    afterFieldChange,
    fieldReadOnly,
  } = surface;
  const bodyValues = useWatch({
    control: form.control,
    disabled: !bodyField?.resolve,
  }) as FormValues;
  const currentBodyField = bodyField
    ? resolveField(bodyField, bodyValues)
    : undefined;
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
        render={({ field: controller, fieldState }) => (
          <BoundFieldRow
            field={field}
            relation={relation}
            selectedOption={selectedOption}
            value={controller.value}
            readOnly={fieldReadOnly(field)}
            errors={fieldState.error ? [fieldState.error] : []}
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
      (section) =>
        section.label != null
        && (section.fields.length > 0 || section.render !== undefined),
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
      {currentBodyField ? (
        <section className="grid gap-2">
          {currentBodyField.label ? (
            <SectionEyebrow as="span">{currentBodyField.label}</SectionEyebrow>
          ) : null}
          <Controller
            control={form.control}
            name={currentBodyField.name}
            render={({ field: controller, fieldState }) => (
              <BodyFieldControl
                field={currentBodyField}
                value={controller.value}
                readOnly={fieldReadOnly(currentBodyField)}
                errors={fieldState.error ? [fieldState.error] : []}
                onChange={(next) => {
                  clearServerFieldError(currentBodyField.name);
                  controller.onChange(next);
                  afterFieldChange(currentBodyField, next);
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
  if (section.fields.length === 0 && section.render === undefined) return null;
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
      {section.fields.length > 0 ? (
        <FormGrid
          columns={section.columns === 1 ? "one" : "two"}
          density="comfortable"
          className="gap-x-8 gap-y-4 pb-2"
        >
          {section.fields.map((field) => renderField(field))}
        </FormGrid>
      ) : null}
      {section.render?.()}
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
          <Tabs.Tab
            key={section.key}
            value={section.key}
            icon={renderGlyph(section.icon)}
          >
            {section.label}
            {section.badge != null ? (
              <Tabs.Count>{section.badge}</Tabs.Count>
            ) : null}
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
  const composite = Boolean(field.objectTemplate || field.itemTemplate || "rowTemplate" in field);
  const messages = [...fieldErrorMessages(errors, composite ? field.name : undefined), ...(serverMessages ?? [])];
  const displayedMessages = composite
    ? directDottedPathMessages(messages, field.name)
    : messages;
  return (
    <FieldRoot
      invalid={displayedMessages.length > 0}
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
        <DescriptorPresenceControl field={field} value={value} readOnly={effectiveReadOnly} onChange={onChange}>
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
            messages={messages}
            readOnly={effectiveReadOnly}
            onChange={onChange}
            controlProps={field.required ? { id: field.name, "aria-required": true } : undefined}
          />
        )}
        </DescriptorPresenceControl>
      </div>
      <FieldFooter description={field.description} errors={displayedMessages} />
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
  const composite = Boolean(field.objectTemplate || field.itemTemplate || "rowTemplate" in field);
  const messages = [...fieldErrorMessages(errors, composite ? field.name : undefined), ...(serverMessages ?? [])];
  return (
    <FieldRoot invalid={messages.length > 0} className="grid gap-2">
      <DescriptorPresenceControl field={field} value={value} readOnly={readOnly} onChange={onChange}>
      <FieldDescriptorControl
        field={field}
        value={value}
        messages={messages}
        readOnly={readOnly}
        onChange={onChange}
      />
      </DescriptorPresenceControl>
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
