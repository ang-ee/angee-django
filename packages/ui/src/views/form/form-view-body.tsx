import * as React from "react";
import { Controller, get, useFormState, useWatch, type Control } from "react-hook-form";
import { rowValueAtPath } from "@angee/metadata";

// Render-only bindings for the headless FormView surface.

import { Textarea } from "../../ui/textarea";
import { Chip } from "../../ui/chip";
import {
  FieldDescription,
  FieldLabel,
  FieldRoot,
} from "../../ui/field";
import { FormGrid } from "../../ui/form-layout";
import { Skeleton, SkeletonStatus } from "../../ui/skeleton";
import { Collapsible } from "../../ui/collapsible";
import { useDeveloperFieldTitle } from "../../chrome/DeveloperMode";
import { textRoleVariants } from "../../ui/text";
import { cn } from "../../lib/cn";
import { optionLabel, relationValueId } from "../../widgets/types";
import { useStatusTone } from "../../widgets/use-status-tone";
import { FieldDescriptorControl } from "./field-descriptor-control";
import type { RelationOption } from "../../widgets/RelationField";
import {
  EditableLines,
  type EditableLineSupplementalColumn,
  type EditableLinesProps,
} from "./EditableLines";
import { DescriptorPresenceControl } from "./descriptor-presence-control";
import { fieldWidgetId, type FieldDescriptor } from "../page";
import type { RelationFieldInfo } from "../resource/model-metadata-defaults";
import { RelationFieldWidget } from "../relation/RelationFieldWidget";
import { RelationMultiFieldWidget } from "../relation/RelationMultiFieldWidget";
import { relationSelectedOption } from "../relation/relation-options";
import {
  fieldAriaLabel,
  fieldErrorMessages,
  gridFieldClass,
  isCompositeFieldDescriptor,
  recordRepresentationValue,
  relationListValue,
  resolveField,
  titleText,
  EDITABLE_LINES_SECTION,
  type FormSectionModel,
  type FormValues,
} from "./form-view-model";
import type { FormViewSurface } from "./form-view-surface";
import { directDottedPathMessages } from "./validation-errors";
import { SectionHeading } from "./SectionHeading";
import { AbsoluteTime } from "../../fragments/AbsoluteTime";
import { RecordHeader } from "../../fragments/RecordHeader";
import { RecordFieldMarkControl } from "./record-field-marks";

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
const FIELD_CONTROL_CLASS = "min-w-0";

function WrappingTitleEditor({
  value,
  controlRef,
  ...props
}: React.ComponentProps<typeof Textarea> & {
  value: string;
  controlRef?: (element: HTMLTextAreaElement | null) => void;
}): React.ReactElement {
  const editor = React.useRef<HTMLTextAreaElement>(null);
  const resize = React.useCallback(() => {
    const element = editor.current;
    if (!element) return;
    element.style.height = "auto";
    element.style.height = `${element.scrollHeight}px`;
  }, []);
  React.useLayoutEffect(resize, [resize, value]);
  React.useEffect(() => {
    const element = editor.current;
    if (!element || typeof ResizeObserver === "undefined") return;
    let width = element.clientWidth;
    const observer = new ResizeObserver(() => {
      if (element.clientWidth === width) return;
      width = element.clientWidth;
      resize();
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, [resize]);
  return <Textarea {...props} value={value} rows={1} resize="none" ref={(element) => {
    editor.current = element;
    controlRef?.(element);
  }} />;
}

/** Shared centered body width for the form and its saved-record panels. */
export const FORM_VIEW_COLUMN_CLASS =
  "mx-auto w-full max-w-[1100px] px-6 sm:px-8";

/** The form's record header on the shared `RecordHeader`: eyebrow · title (read, editable, or a
 * relation) with its marks · statusbar on the title row · facts line · header extras.
 * @owner RecordHeader
 */
export function FormViewRecordHeader({
  surface,
  compact = false,
  awaiting = false,
  title,
  eyebrow,
  extra,
  contextLine,
}: {
  surface: FormViewSurface;
  compact?: boolean;
  /** The saved record has not arrived yet: render the header's shape. */
  awaiting?: boolean;
  title?: React.ReactNode;
  /** The eyebrow above the title: the record's kind and container. */
  eyebrow?: React.ReactNode;
  extra?: React.ReactNode;
  contextLine?: React.ReactNode;
}): React.ReactElement {
  const statusTone = useStatusTone();
  const {
    t,
    form,
    titleField,
    titlePlacementField,
    titleFieldMessages,
    displayRecord,
    modelMetadata,
    contextFields,
    attributionFields,
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
    disabled: !titleField?.resolve && !statusField?.resolve && !statusField?.showWhen,
  }) as FormValues;
  const currentTitleField = titleField
    ? resolveField(titleField, headerValues)
    : undefined;
  const resolvedStatusField = statusField
    ? resolveField(statusField, headerValues)
    : undefined;
  const currentStatusField = resolvedStatusField && !resolvedStatusField.hidden
    && (!resolvedStatusField.showWhen || resolvedStatusField.showWhen(headerValues))
    ? resolvedStatusField : undefined;
  const statusRestricted = Boolean(currentStatusField && fieldIsRedacted(displayRecord, currentStatusField.name));
  const titleRestricted = Boolean(currentTitleField && fieldIsRedacted(displayRecord, currentTitleField.name));
  const titlePlacementRestricted = Boolean(titlePlacementField && fieldIsRedacted(displayRecord, titlePlacementField.name));
  const statusbar = currentStatusField && fieldWidgetId(currentStatusField) === "statusbar";
  const statusOnTitleRow = statusbar && !compact;
  const compactStatus = statusbar && compact;
  const titleRelation = currentTitleField
    ? relationByField.get(currentTitleField.name)
    : undefined;
  const titleSelectedOption = currentTitleField && titleRelation
    ? relationSelectedOption(
        get(displayRecord ?? {}, currentTitleField.name),
        titleRelation.labelField,
      )
    : undefined;
  const statusContainerRef = React.useRef<HTMLDivElement>(null);
  const [statusContainerWidth, setStatusContainerWidth] = React.useState<number>();
  React.useEffect(() => {
    const container = statusContainerRef.current;
    if (!container || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(([entry]) => {
      if (entry) setStatusContainerWidth(entry.contentRect.width);
    });
    observer.observe(container);
    return () => observer.disconnect();
  }, [awaiting, compact, currentStatusField]);
  const density = compact ? "compact" : "comfortable";
  if (awaiting) {
    return (
      <RecordHeader density={density} className="border-b-0 px-0" title={
        <SkeletonStatus label={t("form.loading")} className={cn("grid", compact ? "gap-1" : "gap-3")}>
          {statusField && !compact ? <Skeleton className="h-6 w-72" /> : null}
          <Skeleton shape="text" className={compact ? "h-5 w-1/2" : "h-7 w-2/3"} />
          {!compact ? <Skeleton shape="text" className="h-3 w-64" /> : null}
        </SkeletonStatus>
      } />
    );
  }
  const statusMark = currentStatusField ? <RecordFieldMarkControl field={currentStatusField.name} label={currentStatusField.label}
    restricted={statusRestricted} /> : null;
  const status = currentStatusField ? <div ref={compactStatus ? undefined : statusContainerRef} className={cn(
    compactStatus ? "contents" : "flex min-w-0 flex-wrap items-center gap-3",
    statusOnTitleRow ? "flex-auto justify-end" : "w-full",
  )}>
    {statusRestricted ? <span className="text-13 text-fg-muted">{currentStatusField.label}</span>
      : <Controller control={form.control} name={currentStatusField.name} render={({ field: controller }) =>
      compactStatus ? (
        typeof controller.value === "string" && controller.value ? <Chip
          tone={statusTone(controller.value)} className="justify-self-start"
        >{optionLabel(currentStatusField.options, controller.value)}</Chip> : <span aria-hidden />
      ) : <FieldDescriptorControl
        controlRef={controller.ref}
        field={{ ...currentStatusField, containerWidth: statusContainerWidth }}
        value={controller.value}
        row={displayRecord ?? undefined}
        readOnly={fieldReadOnly(currentStatusField)}
        onChange={(next) => {
          startFieldInteraction(currentStatusField.name);
          clearServerFieldError(currentStatusField.name);
          controller.onChange(next);
          afterFieldChange(currentStatusField, next);
        }}
        onCommit={() => commitFieldInteraction(currentStatusField.name)}
      />
    } />}
    {statusMark}
  </div> : null;
  const editorClass = cn(textRoleVariants({ role: "display" }), "min-h-7 w-full min-w-0 overflow-hidden rounded-none border-0 bg-transparent px-0 py-0 shadow-none hover:border-transparent focus:border-transparent focus:bg-transparent focus-visible:border-transparent placeholder:text-fg-subtle");
  // The title: a given node, the restricted label, the read-only value, a relation picker, or the editor.
  let titleNode: React.ReactNode;
  let editable = false;
  if (title !== undefined) {
    titleNode = title;
  } else if (currentTitleField) {
    titleNode = (
      <Controller
        control={form.control}
        name={currentTitleField.name}
        render={({ field: controller }) =>
          titleRestricted ? (
            <>{currentTitleField.label ?? t("form.record")}</>
          ) : fieldReadOnly(currentTitleField) ? (
            <>{titleText(
              titleRelation
                ? titleSelectedOption?.label ?? relationValueId(controller.value)
                : controller.value,
              t("form.untitled"),
            )}</>
          ) : titleRelation ? (
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
              filters={currentTitleField.filters}
              selectedOption={titleSelectedOption}
              placeholder={currentTitleField.placeholder ?? t("form.untitled")}
              aria-label={fieldAriaLabel(currentTitleField)}
            />
          ) : (
            <WrappingTitleEditor
              controlRef={controller.ref}
              data-form-title="true"
              value={String(controller.value ?? "")}
              placeholder={currentTitleField.placeholder ?? t("form.untitled")}
              aria-label={fieldAriaLabel(currentTitleField)}
              className={editorClass}
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
    );
    editable = !titleRestricted && !fieldReadOnly(currentTitleField);
  } else {
    titleNode = titleText(recordRepresentationValue(displayRecord, modelMetadata), t("form.record"));
  }
  const adornments = <>
    {currentTitleField ? <RecordFieldMarkControl field={currentTitleField.name} label={currentTitleField.label}
      restricted={titleRestricted} /> : null}
    {titlePlacementField && displayRecord && !titlePlacementRestricted ? <FieldDescriptorControl
      field={titlePlacementField}
      value={displayRecord[titlePlacementField.name]}
      row={displayRecord}
      readOnly={fieldReadOnly(titlePlacementField)}
    /> : null}
    {titlePlacementField && titlePlacementRestricted ? <span className="inline-flex items-center gap-2 text-13 text-fg-muted">
      <span>{titlePlacementField.label ?? titlePlacementField.name}</span>
      <RecordFieldMarkControl field={titlePlacementField.name} label={titlePlacementField.label} restricted />
    </span> : null}
  </>;
  // The metadata-owned facts line is the default. A declared context line
  // remains the deliberate escape hatch for a domain-specific sentence.
  const meta = contextLine
    ? <span className="break-words">{contextLine}</span>
    : compact ? undefined : <RecordFactsLine resource={modelMetadata?.resource.modelLabel}
      record={displayRecord} fields={contextFields} attributionFields={attributionFields}
      technicalParts={subtitleParts} loading={loading} loadingLabel={t("form.loading")}
      createdByLabel={t("form.createdBy")} updatedByLabel={t("form.updatedBy")} />;
  const messages = titleField && titleFieldMessages.length > 0 ? (
    <p className="text-12 leading-5 text-danger-text">{titleFieldMessages.join(", ")}</p>
  ) : null;
  return (
    <RecordHeader
      density={density}
      className="border-b-0 px-0"
      headingLevel={editable ? null : 1}
      crumbs={eyebrow}
      lead={!statusOnTitleRow ? status : undefined}
      title={titleNode}
      titleAdornments={adornments}
      status={statusOnTitleRow && status ? status : undefined}
      meta={meta}
      extra={messages || extra ? <>{messages}{extra}</> : undefined}
    />
  );
}

/** Line rendering a form passes through to its editable lines, wherever they render. */
export interface FormViewLineProps {
  linePrimaryFields?: readonly string[];
  lineFields?: EditableLinesProps["fields"];
  lineSupplementalColumns?: readonly EditableLineSupplementalColumn[];
  lineRelationFilters?: EditableLinesProps["relationFilters"];
  /** Rendered under the lines only (totals belong to the lines, not to the whole record). */
  lineFooter?: EditableLinesProps["footer"];
}

/** The form's editable lines bound to its draft: in a declared `lines` section or their trailing pane. */
export function FormViewLines({
  surface,
  linePrimaryFields,
  lineFields,
  lineSupplementalColumns,
  lineRelationFilters,
  lineFooter,
}: FormViewLineProps & { surface: FormViewSurface }): React.ReactElement | null {
  const { form, linesActive, linesResource, linesField, formReadOnly, lineRowErrors } = surface;
  if (!linesActive || !linesResource || !linesField) return null;
  return (
    <FormEditableLines
      control={form.control}
      setValue={form.setValue}
      name={linesField}
      lines={linesResource}
      parentRow={surface.displayRecord}
      readOnly={formReadOnly}
      rowErrors={lineRowErrors}
      primaryFields={linePrimaryFields}
      fields={lineFields}
      supplementalColumns={lineSupplementalColumns}
      relationFilters={lineRelationFilters}
      footer={lineFooter}
    />
  );
}

/**
 * The trailing lines' pane. It stays mounted beside other panes, so a save whose lines
 * fail, in the browser or on the server, selects it and the viewer sees the rows to fix.
 */
export function FormViewLinesPane(props: FormViewLineProps & { surface: FormViewSurface }): React.ReactElement | null {
  const { form: { control }, linesField, setActiveRecordTab, tabbed } = props.surface;
  const { errors, submitCount } = useFormState({ control, name: linesField ?? undefined, disabled: !linesField });
  const linesFailed = tabbed && linesField !== null && submitCount > 0 && get(errors, linesField) !== undefined;
  React.useEffect(() => {
    if (linesFailed) setActiveRecordTab(EDITABLE_LINES_SECTION);
  }, [linesFailed, setActiveRecordTab, submitCount]);
  return <FormViewLines {...props} />;
}

/** The record's sheet: its body field and every section, always shown above any panes. */
export function FormViewSheet({
  surface,
  groupLayout,
  ...lineProps
}: FormViewLineProps & {
  surface: FormViewSurface;
  groupLayout: "stacked" | "paired";
}): React.ReactElement {
  const {
    form,
    sections,
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
    disabled: !bodyField,
  }) as FormValues;
  const currentBodyField = bodyField
    ? resolveField(bodyField, bodyValues)
    : undefined;
  const bodyRestricted = Boolean(currentBodyField && fieldIsRedacted(surface.displayRecord, currentBodyField.name));
  const renderField = boundFieldRenderer(surface);
  // A declared `lines` group holds the editable lines in its place in the sheet.
  const sheetSections = sections.map((section) => section.key === EDITABLE_LINES_SECTION
    ? { ...section, render: () => <FormViewLines surface={surface} {...lineProps} /> } : section);
  const pair = groupLayout === "paired"
    ? sheetSections.filter((section) => section.label == null && section.key.startsWith("group:")).slice(0, 2)
    : [];
  const [left, right] = pair;
  const renderSection = (section: FormSectionModel) => (
    <FormSection key={section.key} section={section} renderField={renderField} control={form.control} requestedFocusPath={requestedFocusPath} />
  );
  return (
    <>
      {currentBodyField ? (
        <section className="grid gap-2">
          {currentBodyField.label ? (
            <div className="flex items-center gap-2"><SectionHeading as="h2" label={currentBodyField.label} />
              <RecordFieldMarkControl field={currentBodyField.name} label={currentBodyField.label}
                restricted={bodyRestricted} /></div>
          ) : null}
          {!bodyRestricted ? <Controller
            control={form.control}
            name={currentBodyField.name}
            render={({ field: controller, fieldState }) => (
              <BodyFieldControl
                controlRef={controller.ref}
                field={currentBodyField}
                value={controller.value}
                row={bodyValues}
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
          /> : null}
        </section>
      ) : null}
      <div className="grid gap-6">
        {sheetSections.map((section) => {
          if (left && right && section.key === right.key) return null;
          if (left && right && section.key === left.key) {
            return <FormGrid key="paired-sheet-groups" columns="adaptiveTwo" density="comfortable" className="items-start gap-6">
              {pair.map(renderSection)}
            </FormGrid>;
          }
          return renderSection(section);
        })}
      </div>
    </>
  );
}

/** A `pane` group's fields, bound to the record's form like the sheet's; the pane strip names it. */
export function FormViewPaneGroup({
  surface,
  section,
}: {
  surface: FormViewSurface;
  section: FormSectionModel;
}): React.ReactElement | null {
  return <FormSection section={{ ...section, label: undefined, collapsible: false }}
    renderField={boundFieldRenderer(surface)} control={surface.form.control} requestedFocusPath={surface.requestedFocusPath} />;
}

function boundFieldRenderer(surface: FormViewSurface): (field: FieldDescriptor) => React.ReactNode {
  return (field) => field.hidden ? null : <BoundFormField key={field.name} surface={surface} field={field} />;
}

function BoundFormField({
  surface,
  field,
  rail = false,
}: {
  surface: FormViewSurface;
  field: FieldDescriptor;
  rail?: boolean;
}): React.ReactElement {
  const values = useWatch({ control: surface.form.control });
  const value = get(values, field.name);
  const readOnly = surface.fieldReadOnly(field);
  const relation = surface.relationByField.get(field.name);
  const relationList = surface.relationListByField.get(field.name);
  const currentRelationId = relationValueId(value);
  const savedOption = relation
    ? relationSelectedOption(get(surface.displayRecord ?? {}, field.name), relation.labelField)
    : undefined;
  const selectedOption = relation && currentRelationId
    ? relationSelectedOption(value, relation.labelField)
      ?? (savedOption?.value === currentRelationId
        ? savedOption
        : undefined)
    : undefined;
  return (
    <Controller
      control={surface.form.control}
      name={field.name}
      render={({ field: controller, fieldState }) => (
        <BoundFieldRow
          controlRef={controller.ref}
          field={field}
          rail={rail}
          relation={relation}
          relationList={relationList}
          selectedOption={selectedOption}
          value={value}
          row={values}
          readOnly={readOnly}
          restricted={fieldIsRedacted(surface.displayRecord, field.name)}
          errors={fieldState.error ? [fieldState.error] : []}
          onCommit={() => surface.commitFieldInteraction(field.name)}
          onChange={(picked) => {
            // A re-picked equal set restores the baseline, so it never dirties the form.
            const next = relationList
              ? relationListValue(picked, get(surface.form.formState.defaultValues, field.name))
              : picked;
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

/** Rail fields use the same form controller and widget binding as body fields. */
export function FormViewRail({ surface }: { surface: FormViewSurface }): React.ReactElement | null {
  if (surface.railGroups.length === 0) return null;
  return <aside className="min-w-0 border-t border-border-subtle pt-5 @min-[52rem]:border-l @min-[52rem]:border-t-0 @min-[52rem]:pl-5 @min-[52rem]:pt-0">
    <div className="grid gap-7">
      {surface.railGroups.map((group) => <section key={group.id} className="grid gap-3">
        <SectionHeading label={group.label} summary={group.summary} hint={group.hint} audience={group.audience} />
        {(group.fields ?? []).map(({ field }) => <BoundFormField key={field.name} surface={surface} field={field} rail />)}
        {group.content}
      </section>)}
    </div>
  </aside>;
}

function RecordFactsLine({
  resource,
  record,
  fields,
  attributionFields,
  technicalParts,
  loading,
  loadingLabel,
  createdByLabel,
  updatedByLabel,
}: {
  resource?: string;
  record: FormValues | null;
  fields: readonly FieldDescriptor[];
  attributionFields: readonly FieldDescriptor[];
  technicalParts: readonly React.ReactNode[];
  loading: boolean;
  loadingLabel: React.ReactNode;
  createdByLabel: React.ReactNode;
  updatedByLabel: React.ReactNode;
}): React.ReactElement | null {
  const valueParts = record == null ? [] : fields.flatMap((field) => {
    if (fieldIsRedacted(record, field.name)) return [
      <span key={field.name} className="inline-flex min-w-0 items-center gap-1">
        <span>{field.label ?? field.name}</span>
        <RecordFieldMarkControl field={field.name} label={field.label} restricted />
      </span>,
    ];
    const value = rowValueAtPath(record, field.name);
    return value == null || value === "" ? [] : [
      <FieldDescriptorControl key={field.name} resource={resource} field={field}
        value={value} row={record} readOnly />,
    ];
  });
  const labels = {
    created_by: createdByLabel,
    created_by_label: createdByLabel,
    updated_by: updatedByLabel,
    updated_by_label: updatedByLabel,
  } as const;
  // "created by admin · Oct 20": the by-field leads, its stamp follows as an absolute date.
  const stampFor = (prefix: string) => attributionFields.find((field) => field.name === `${prefix}_at`);
  const attributionParts = record == null ? [] : attributionFields.flatMap((field) => {
    if (field.name.endsWith("_at")) return [];
    const value = rowValueAtPath(record, field.name);
    if (value == null || value === "") return [];
    const stamp = stampFor(field.name.replace(/_by(_label)?$/, ""));
    const stampValue = stamp ? rowValueAtPath(record, stamp.name) : undefined;
    return [<span key={field.name} className="inline-flex min-w-0 items-center gap-1">
      <span>{labels[field.name as keyof typeof labels]}</span>
      <FieldDescriptorControl resource={resource} field={field} value={value} row={record} readOnly />
      {typeof stampValue === "string" && stampValue ? <AbsoluteTime display="date" value={stampValue} /> : null}
    </span>];
  });
  const parts = [...valueParts, ...attributionParts, ...technicalParts];
  if (!loading && parts.length === 0) return null;
  return (
    <div
      className={cn(
        textRoleVariants({ role: "meta" }),
        "mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 [&_[data-slot=field-control]]:min-h-0",
      )}
    >
      {parts.map((part, index) => (
        <React.Fragment key={index}>
          {index > 0 ? <span aria-hidden="true">·</span> : null}
          <span>{part}</span>
        </React.Fragment>
      ))}
      {loading ? (
        <>
          {parts.length > 0 ? <span aria-hidden="true">·</span> : null}
          <SkeletonStatus label={loadingLabel} className="inline-flex min-w-20 items-center">
            <Skeleton className="h-3 w-20" shape="text" />
          </SkeletonStatus>
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
          <SectionHeading as="span" label={section.label} count={section.badge} hint={section.hint} audience={section.audience} />
        </Collapsible.Trigger>
        <Collapsible.Panel keepMounted>{content}</Collapsible.Panel>
      </Collapsible.Root>
    );
  }
  return (
    <section className="grid gap-3">
      {section.label ? (
        <SectionHeading label={section.label} count={section.badge} hint={section.hint} audience={section.audience}
          className="border-b border-border-subtle pb-1" />
      ) : null}
      {content}
    </section>
  );
}

function BoundFieldRow({
  field,
  rail = false,
  relation,
  relationList,
  selectedOption,
  value,
  row,
  readOnly,
  restricted = false,
  errors,
  serverMessages,
  onChange,
  onCommit,
  controlRef,
}: {
  field: FieldDescriptor;
  rail?: boolean;
  relation?: RelationFieldInfo;
  relationList?: RelationFieldInfo;
  selectedOption?: RelationOption;
  value: unknown;
  row: FormValues;
  readOnly?: boolean;
  restricted?: boolean;
  errors: readonly unknown[];
  serverMessages?: readonly string[];
  onChange: (value: unknown) => void;
  onCommit?: () => void;
  controlRef?: (target: import("../../widgets").WidgetFocusTarget | null) => void;
}): React.ReactElement {
  const effectiveReadOnly = Boolean(readOnly);
  const developerTitle = useDeveloperFieldTitle();
  const composite = isCompositeFieldDescriptor(field);
  const messages = [...fieldErrorMessages(errors, composite ? field.name : undefined), ...(serverMessages ?? [])];
  const displayedMessages = composite
    ? directDottedPathMessages(messages, field.name)
    : messages;
  return (
    <FieldRoot
      invalid={displayedMessages.length > 0}
      className={cn(FIELD_ROOT_CLASS, gridFieldClass(field), rail && "grid grid-cols-[minmax(0,5.5rem)_minmax(0,1fr)] items-start gap-x-2")}
    >
      <div className="flex items-center gap-1"><FieldLabel variant="eyebrow" className={cn(rail && "mb-0 min-h-8 normal-case tracking-normal")}
        title={developerTitle(field.name, field.widget)}>
        {field.label ?? field.name}
      </FieldLabel>
      <RecordFieldMarkControl field={field.name} label={field.label} restricted={restricted} /></div>
      {!restricted ? <div
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
            filters={field.filters}
            selectedOption={selectedOption}
            aria-label={fieldAriaLabel(field)}
          />
        ) : relationList ? (
          <RelationMultiFieldWidget
            controlRef={controlRef}
            value={Array.isArray(value) ? value : []}
            onChange={onChange}
            onCommit={onCommit}
            readOnly={effectiveReadOnly}
            relation={relationList}
            filters={field.filters}
            aria-label={fieldAriaLabel(field)}
          />
        ) : (
          <FieldDescriptorControl
            controlRef={controlRef}
            field={field}
            value={value}
            row={row}
            messages={messages}
            readOnly={effectiveReadOnly}
            onChange={onChange}
            onCommit={onCommit}
            controlProps={field.required ? { id: field.name, "aria-required": true } : undefined}
          />
        )}
        </DescriptorPresenceControl>
      </div> : null}
      <FieldFooter description={field.description} errors={displayedMessages} />
    </FieldRoot>
  );
}

function fieldIsRedacted(record: FormValues | null | undefined, field: string): boolean {
  const redacted = record?.redacted_fields;
  return Array.isArray(redacted) && redacted.includes(field.split(".", 1)[0]);
}

function BodyFieldControl({
  field,
  value,
  row,
  readOnly,
  errors,
  serverMessages,
  onChange,
  onCommit,
  controlRef,
}: {
  field: FieldDescriptor;
  value: unknown;
  row: FormValues;
  readOnly?: boolean;
  errors: readonly unknown[];
  serverMessages?: readonly string[];
  onChange: (value: unknown) => void;
  onCommit?: () => void;
  controlRef?: (target: import("../../widgets").WidgetFocusTarget | null) => void;
}): React.ReactElement {
  const composite = isCompositeFieldDescriptor(field);
  const messages = [...fieldErrorMessages(errors, composite ? field.name : undefined), ...(serverMessages ?? [])];
  return (
    <FieldRoot invalid={messages.length > 0} className="grid gap-2">
      <DescriptorPresenceControl field={field} value={value} readOnly={readOnly} onChange={onChange} onCommit={onCommit} controlRef={controlRef}>
      <FieldDescriptorControl
        controlRef={controlRef}
        field={field}
        value={value}
        row={row}
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
        <p className="text-12 leading-5 text-danger-text">{errors.join(", ")}</p>
      ) : null}
    </>
  );
}


/** Watch the draft only inside the lines boundary, leaving sheet fields unsubscribed. */
function FormEditableLines({ control, parentRow, ...props }: EditableLinesProps): React.ReactElement {
  const draft = useWatch({ control });
  return <EditableLines {...props} control={control} parentRow={{ ...parentRow, ...draft }} />;
}
