import * as React from "react";
import { Controller, get, useFormState, useWatch, type Control } from "react-hook-form";

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
import { Collapsible } from "../../ui/collapsible";
import { renderGlyph } from "../../chrome/Glyph";
import { textRoleVariants } from "../../ui/text";
import { cn } from "../../lib/cn";
import { optionLabel, relationValueId } from "../../widgets/types";
import { statusTone } from "../../widgets/status-tones";
import type { RelationOption } from "../../widgets/RelationField";
import { EditableLines, type EditableLinesProps } from "./EditableLines";
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
  type FormViewLayout,
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
  title,
}: {
  surface: FormViewSurface;
  compact?: boolean;
  title?: React.ReactNode;
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
    startFieldInteraction,
    commitFieldInteraction,
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
          {title !== undefined ? (
            <h1 className={compact ? "truncate text-base font-semibold text-fg" : TITLE_TEXT_CLASS}>{title}</h1>
          ) : currentTitleField ? (
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
                      controlRef={controller.ref}
                      value={relationValueId(controller.value) || null}
                      onChange={(next) => {
                        startFieldInteraction(currentTitleField.name);
                        clearServerFieldError(currentTitleField.name);
                        controller.onChange(next);
                        afterFieldChange(currentTitleField, next);
                      }}
                      onCommit={() => commitFieldInteraction(currentTitleField.name)}
                      relation={titleRelation}
                      selectedOption={titleSelectedOption}
                      placeholder={currentTitleField.placeholder ?? t("form.untitled")}
                      aria-label={fieldAriaLabel(currentTitleField)}
                    />
                  </div>
                ) : (
                  <Input
                    ref={controller.ref}
                    value={String(controller.value ?? "")}
                    placeholder={currentTitleField.placeholder ?? t("form.untitled")}
                    aria-label={fieldAriaLabel(currentTitleField)}
                    className={cn(
                      compact ? "h-8 border-0 bg-transparent px-0 text-base font-semibold shadow-none" : cn(TITLE_TEXT_CLASS, TITLE_INPUT_CLASS),
                    )}
                    onChange={(event) => {
                      startFieldInteraction(currentTitleField.name);
                      clearServerFieldError(currentTitleField.name);
                      controller.onChange(event.currentTarget.value);
                      afterFieldChange(currentTitleField, event.currentTarget.value);
                    }}
                    onBlur={() => commitFieldInteraction(currentTitleField.name)}
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
                  controlRef={controller.ref}
                  field={currentStatusField}
                  value={controller.value}
                  readOnly={fieldReadOnly(currentStatusField)}
                  onChange={(next) => {
                    startFieldInteraction(currentStatusField.name);
                    controller.onChange(next);
                    afterFieldChange(currentStatusField, next);
                  }}
                  onCommit={() => commitFieldInteraction(currentStatusField.name)}
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
  layout: FormViewLayout;
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
    startFieldInteraction,
    commitFieldInteraction,
    requestedFocusPath,
  } = surface;
  const bodyValues = useWatch({
    control: form.control,
    disabled: !bodyField?.resolve,
  }) as FormValues;
  const currentBodyField = bodyField
    ? resolveField(bodyField, bodyValues)
    : undefined;
  const renderField = (field: FieldDescriptor): React.ReactNode => {
    if (field.hidden) return null;
    const relation = surface.relationByField.get(field.name);
    return (
      <BoundFormField
        key={field.name}
        surface={surface}
        field={field}
        relation={relation}
      />
    );
  };
  const renderSections = (list: readonly FormSectionModel[]): React.ReactNode => {
    if (layout === "sidebar") {
      // The standing column holds what people check and change on every visit;
      // the rest keeps the tabbed body, so a record with a long tail does not
      // pay for it on first read.
      const properties = list.filter((section) => section.placement === "properties");
      const main = list.filter((section) => section.placement !== "properties");
      return (
        <div className="form-sidebar-grid">
          <div className="grid min-w-0 gap-6">{renderTabbed(main)}</div>
          {properties.length > 0 ? (
            <aside className="grid min-w-0 gap-4">
              {properties.map((section) => (
                <FormSection key={section.key} section={section} renderField={renderField} control={form.control} requestedFocusPath={requestedFocusPath} />
              ))}
            </aside>
          ) : null}
        </div>
      );
    }
    return renderTabbed(list);
  };
  const renderTabbed = (list: readonly FormSectionModel[]): React.ReactNode => {
    if (layout === "stacked") {
      return list.map((section) => (
        <FormSection key={section.key} section={section} renderField={renderField} control={form.control} requestedFocusPath={requestedFocusPath} />
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
          <FormSection key={section.key} section={section} renderField={renderField} control={form.control} requestedFocusPath={requestedFocusPath} />
        ))}
        {tabbedSections.length > 0 ? (
          <FormSectionTabs sections={tabbedSections} renderField={renderField} control={form.control} requestedFocusPath={requestedFocusPath} />
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
          <FormEditableLines
            control={form.control}
            setValue={form.setValue}
            name={linesField}
            lines={linesResource}
            parentRow={surface.displayRecord}
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
                controlRef={controller.ref}
                field={currentBodyField}
                value={controller.value}
                readOnly={fieldReadOnly(currentBodyField)}
                errors={fieldState.error ? [fieldState.error] : []}
                onCommit={() => commitFieldInteraction(currentBodyField.name)}
                onChange={(next) => {
                  startFieldInteraction(currentBodyField.name);
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

function BoundFormField({
  surface,
  field,
  relation,
}: {
  surface: FormViewSurface;
  field: FieldDescriptor;
  relation: RelationFieldInfo | undefined;
}): React.ReactElement {
  const value = useWatch({ control: surface.form.control, name: field.name });
  const readOnly = surface.fieldReadOnly(field);
  const currentRelationId = relationValueId(value);
  const savedOption = relation
    ? relationSelectedOption(surface.displayRecord?.[field.name], relation.labelField)
    : undefined;
  const selectedOption = relation && currentRelationId
    ? relationSelectedOption(value, relation.labelField)
      ?? (savedOption?.value === currentRelationId
        ? savedOption
        : { value: currentRelationId, label: currentRelationId })
    : undefined;
  return (
    <Controller
      control={surface.form.control}
      name={field.name}
      render={({ field: controller, fieldState }) => (
        <BoundFieldRow
          controlRef={controller.ref}
          field={field}
          relation={relation}
          selectedOption={selectedOption}
          value={value}
          readOnly={readOnly}
          errors={fieldState.error ? [fieldState.error] : []}
          onCommit={() => surface.commitFieldInteraction(field.name)}
          onChange={(next) => {
            surface.startFieldInteraction(field.name);
            surface.clearServerFieldError(field.name);
            controller.onChange(next);
            surface.afterFieldChange(field, next);
          }}
        />
      )}
    />
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
  control,
  requestedFocusPath,
}: {
  section: FormSectionModel;
  renderField: (field: FieldDescriptor) => React.ReactNode;
  control: Control<FormValues>;
  requestedFocusPath: string | null;
}): React.ReactElement | null {
  const { errors } = useFormState({ control, name: section.fields.map((field) => field.name) });
  const hasErrors = section.fields.some((field) => get(errors, field.name) !== undefined);
  const [open, setOpen] = React.useState(section.defaultOpen ?? false);
  React.useEffect(() => { if (hasErrors) setOpen(true); }, [hasErrors]);
  React.useEffect(() => {
    if (requestedFocusPath && section.fields.some((field) => requestedFocusPath === field.name || requestedFocusPath.startsWith(`${field.name}.`))) {
      setOpen(true);
    }
  }, [requestedFocusPath, section.fields]);
  if (section.fields.length === 0 && section.render === undefined) return null;
  const content = (
    <>
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
    </>
  );
  if (section.collapsible && section.label) {
    return (
      <Collapsible.Root open={open} onOpenChange={setOpen} className="grid gap-3">
        <Collapsible.Trigger className="flex items-center gap-2 border-b border-border-subtle pb-1">
          <Collapsible.Icon />
          <SectionEyebrow as="span" spacing="field" tracking="wide" weight="semibold">
            {section.label}
          </SectionEyebrow>
        </Collapsible.Trigger>
        <Collapsible.Panel keepMounted>{content}</Collapsible.Panel>
      </Collapsible.Root>
    );
  }
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
      {content}
    </section>
  );
}

function FormSectionTabs({
  sections,
  renderField,
  control,
  requestedFocusPath,
}: {
  sections: readonly FormSectionModel[];
  renderField: (field: FieldDescriptor) => React.ReactNode;
  control: Control<FormValues>;
  requestedFocusPath: string | null;
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
            control={control}
            requestedFocusPath={requestedFocusPath}
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
  onCommit,
  controlRef,
}: {
  field: FieldDescriptor;
  relation?: RelationFieldInfo;
  selectedOption?: RelationOption;
  value: unknown;
  readOnly?: boolean;
  errors: readonly unknown[];
  serverMessages?: readonly string[];
  onChange: (value: unknown) => void;
  onCommit?: () => void;
  controlRef?: (target: import("../../widgets").WidgetFocusTarget | null) => void;
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
      <DescriptorPresenceControl field={field} value={value} readOnly={effectiveReadOnly} onChange={onChange} onCommit={onCommit} controlRef={controlRef}>
        {relation && (!field.widget || field.widget === "many2one") ? (
          <RelationFieldWidget
            controlRef={controlRef}
            value={relationValueId(value) || null}
            onChange={onChange}
            onCommit={onCommit}
            readOnly={effectiveReadOnly}
            relation={relation}
            selectedOption={selectedOption}
            aria-label={fieldAriaLabel(field)}
          />
        ) : (
          <FieldDescriptorControl
            controlRef={controlRef}
            field={field}
            value={value}
            messages={messages}
            readOnly={effectiveReadOnly}
            onChange={onChange}
            onCommit={onCommit}
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
  onCommit,
  controlRef,
}: {
  field: FieldDescriptor;
  value: unknown;
  readOnly?: boolean;
  errors: readonly unknown[];
  serverMessages?: readonly string[];
  onChange: (value: unknown) => void;
  onCommit?: () => void;
  controlRef?: (target: import("../../widgets").WidgetFocusTarget | null) => void;
}): React.ReactElement {
  const composite = Boolean(field.objectTemplate || field.itemTemplate || "rowTemplate" in field);
  const messages = [...fieldErrorMessages(errors, composite ? field.name : undefined), ...(serverMessages ?? [])];
  return (
    <FieldRoot invalid={messages.length > 0} className="grid gap-2">
      <DescriptorPresenceControl field={field} value={value} readOnly={readOnly} onChange={onChange} onCommit={onCommit} controlRef={controlRef}>
      <FieldDescriptorControl
        controlRef={controlRef}
        field={field}
        value={value}
        messages={messages}
        readOnly={readOnly}
        onChange={onChange}
        onCommit={onCommit}
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


/** Watch the draft only inside the lines boundary, leaving overview fields unsubscribed. */
function FormEditableLines({ control, parentRow, ...props }: EditableLinesProps): React.ReactElement {
  const draft = useWatch({ control });
  return <EditableLines {...props} control={control} parentRow={{ ...parentRow, ...draft }} />;
}
