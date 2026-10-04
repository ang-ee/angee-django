import * as React from "react";
import { Controller, get, useFormState, useWatch, type Control } from "react-hook-form";

// Render-only bindings for the headless FormView surface.

import { Textarea } from "../../ui/textarea";
import { Badge } from "../../ui/badge";
import {
  FieldDescription,
  FieldLabel,
  FieldRoot,
} from "../../ui/field";
import { FormGrid } from "../../ui/form-layout";
import { Skeleton, SkeletonStatus } from "../../ui/skeleton";
import { Tabs } from "../../ui/tabs";
import { Collapsible } from "../../ui/collapsible";
import { renderGlyph } from "../../chrome/Glyph";
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
import { relationSelectedOption } from "../relation/relation-options";
import {
  fieldAriaLabel,
  fieldErrorMessages,
  gridFieldClass,
  isCompositeFieldDescriptor,
  recordRepresentationValue,
  resolveField,
  titleText,
  visibleSections,
  type FormSectionModel,
  type FormValues,
} from "./form-view-model";
import type { FormViewSurface, RecordToolbarContext } from "./form-view-surface";
import { directDottedPathMessages } from "./validation-errors";
import { SectionHeading } from "./SectionHeading";
import { RecordFieldMarkButton } from "./record-field-marks";

const TITLE_TEXT_CLASS =
  "block w-full min-w-0 break-words text-28 font-semibold leading-9 text-fg";
const TITLE_EDITOR_CLASS =
  "min-h-9 overflow-hidden rounded-none border-0 bg-transparent px-0 py-0 shadow-none " +
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

export function FormViewRecordHeader({
  surface,
  compact = false,
  awaiting = false,
  title,
  extra,
  contextLine,
}: {
  surface: FormViewSurface;
  compact?: boolean;
  /** The saved record has not arrived yet: render the header's shape. */
  awaiting?: boolean;
  title?: React.ReactNode;
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
  if (awaiting) {
    return (
      <header className={cn("grid", compact ? "gap-1" : "gap-4")}>
        <SkeletonStatus label={t("form.loading")} className={cn("grid", compact ? "gap-1" : "gap-3")}>
          {statusField && !compact ? <Skeleton className="h-6 w-72" /> : null}
          <Skeleton shape="text" className={compact ? "h-5 w-1/2" : "h-9 w-2/3"} />
          {!compact ? <Skeleton shape="text" className="h-3 w-64" /> : null}
        </SkeletonStatus>
      </header>
    );
  }
  return (
    <header className={cn("grid", compact ? "gap-1" : "gap-4")}>
      {currentStatusField && compact && fieldWidgetId(currentStatusField) === "statusbar" ? (
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
        <div ref={statusContainerRef} className="flex min-w-0 w-full flex-wrap items-center gap-3">
          <Controller
            control={form.control}
            name={currentStatusField.name}
            render={({ field: controller }) => (
              <FieldDescriptorControl
                controlRef={controller.ref}
                field={{ ...currentStatusField, containerWidth: statusContainerWidth }}
                value={controller.value}
                row={displayRecord ?? undefined}
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
      {currentStatusField ? <RecordFieldMarkButton field={currentStatusField.name} label={currentStatusField.label} /> : null}
      <div className="min-w-0 flex-1 self-start">
        <div className="flex min-w-0 items-center gap-3">
          <div className="min-w-0 flex-1">
        {title !== undefined ? (
          <h1 className={compact ? "break-words text-base font-semibold text-fg" : TITLE_TEXT_CLASS}>{title}</h1>
        ) : currentTitleField ? (
          <Controller
            control={form.control}
            name={currentTitleField.name}
            render={({ field: controller }) =>
              fieldReadOnly(currentTitleField) ? (
                <h1 className={compact ? "break-words text-base font-semibold text-fg" : TITLE_TEXT_CLASS}>
                  {titleText(
                    titleRelation
                      ? titleSelectedOption?.label ?? relationValueId(controller.value)
                      : controller.value,
                    t("form.untitled"),
                  )}
                </h1>
              ) : titleRelation ? (
                <div className={compact ? "min-w-0 break-words text-base font-semibold" : TITLE_TEXT_CLASS}>
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
                </div>
              ) : (
                <WrappingTitleEditor
                  controlRef={controller.ref}
                  data-form-title="true"
                  value={String(controller.value ?? "")}
                  placeholder={currentTitleField.placeholder ?? t("form.untitled")}
                  aria-label={fieldAriaLabel(currentTitleField)}
                  className={cn(
                    compact ? "min-h-8 overflow-hidden border-0 bg-transparent px-0 text-base font-semibold shadow-none" : cn(TITLE_TEXT_CLASS, TITLE_EDITOR_CLASS),
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
          <h1 className={compact ? "break-words text-base font-semibold text-fg" : TITLE_TEXT_CLASS}>
            {titleText(
              recordRepresentationValue(displayRecord, modelMetadata),
              t("form.record"),
            )}
          </h1>
        )}
          </div>
          {currentTitleField ? <RecordFieldMarkButton field={currentTitleField.name} label={currentTitleField.label} /> : null}
          {titlePlacementField && displayRecord ? <FieldDescriptorControl
            field={titlePlacementField}
            value={displayRecord[titlePlacementField.name]}
            row={displayRecord}
            readOnly={fieldReadOnly(titlePlacementField)}
          /> : null}
        </div>
        {titleField && titleFieldMessages.length > 0 ? (
          <p className="mt-1 text-xs leading-5 text-danger-text">
            {titleFieldMessages.join(", ")}
          </p>
        ) : null}
        {/* A declared context line is the record's one compact subtitle; the
            generic id/created/updated line is the fallback for forms without one. */}
        {!compact && !contextLine ? <RecordSubtitle loading={loading} loadingLabel={t("form.loading")} parts={subtitleParts} /> : null}
        {contextLine ? <div className="mt-1 break-words text-xs text-fg-muted">{contextLine}</div> : null}
      </div>
      {extra ? <div className={compact ? "pt-1" : undefined}>{extra}</div> : null}
    </header>
  );
}

export function FormViewOverview({
  surface,
  layout,
  groupLayout,
  bodyTabs,
  linesTabLabel,
  linePrimaryFields,
  lineSupplementalColumns,
  lineRelationFilters,
  context,
}: {
  surface: FormViewSurface;
  layout: "stacked" | "tabs";
  groupLayout: "stacked" | "paired";
  bodyTabs?: readonly { id: string; label: React.ReactNode; render: (context: RecordToolbarContext) => React.ReactNode }[];
  linesTabLabel?: React.ReactNode;
  linePrimaryFields?: readonly string[];
  lineSupplementalColumns?: readonly EditableLineSupplementalColumn[];
  lineRelationFilters?: EditableLinesProps["relationFilters"];
  context: RecordToolbarContext;
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
    disabled: !bodyField,
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
  const editableLines = linesActive && linesResource && linesField ? (
    <FormEditableLines
      control={form.control}
      setValue={form.setValue}
      name={linesField}
      lines={linesResource}
      parentRow={surface.displayRecord}
      readOnly={formReadOnly}
      rowErrors={lineRowErrors}
      primaryFields={linePrimaryFields}
      supplementalColumns={lineSupplementalColumns}
      relationFilters={lineRelationFilters}
    />
  ) : null;
  const renderOverviewSections = (list: readonly FormSectionModel[]): React.ReactNode => {
    const pair = groupLayout === "paired"
      ? list.filter((section) => section.label == null && section.key.startsWith("group:")).slice(0, 2)
      : [];
    const [left, right] = pair;
    if (!left || !right) {
      return list.map((section) => (
        <FormSection key={section.key} section={section} renderField={renderField} control={form.control} requestedFocusPath={requestedFocusPath} />
      ));
    }
    return list.map((section) => {
      if (section.key === right.key) return null;
      if (section.key === left.key) {
        return <FormGrid key="paired-overview-groups" columns="adaptiveTwo" density="comfortable" className="items-start gap-6">
          {pair.map((group) => (
            <FormSection key={group.key} section={group} renderField={renderField} control={form.control} requestedFocusPath={requestedFocusPath} />
          ))}
        </FormGrid>;
      }
      return <FormSection key={section.key} section={section} renderField={renderField} control={form.control} requestedFocusPath={requestedFocusPath} />;
    });
  };
  const renderSections = (list: readonly FormSectionModel[]): React.ReactNode => {
    if (layout !== "tabs") {
      return renderOverviewSections(list);
    }
    const stacked = list.filter((section) => section.label == null || section.collapsible);
    const groupTabs = list.filter(
      (section) =>
        section.label != null
        && !section.collapsible
        && (section.fields.length > 0 || section.render !== undefined),
    );
    const tabbedSections: FormSectionModel[] = [
      ...(editableLines ? [{ key: "editable-lines", label: linesTabLabel ?? t("lines.section"), fields: [], render: () => editableLines }] : []),
      ...(bodyTabs ?? []).map((tab) => ({ key: tab.id, label: tab.label, fields: [], render: () => tab.render(context) })),
      ...groupTabs,
    ];
    return (
      <>
        {renderOverviewSections(stacked)}
        {tabbedSections.length > 0 ? (
          <FormSectionTabs
            sections={tabbedSections} renderField={renderField} control={form.control}
            requestedFocusPath={requestedFocusPath} lineField={linesField}
          />
        ) : null}
      </>
    );
  };

  return (
    <>
      {currentBodyField ? (
        <section className="grid gap-2">
          {currentBodyField.label ? (
            <div className="flex items-center gap-2"><SectionHeading as="h2" label={currentBodyField.label} />
              <RecordFieldMarkButton field={currentBodyField.name} label={currentBodyField.label} /></div>
          ) : null}
          <Controller
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
          />
        </section>
      ) : null}
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
      {layout !== "tabs" && editableLines ? (
        <section className="grid gap-3">
          <SectionHeading label={t("lines.section")}
            className="border-b border-border-subtle pb-1" />
          {editableLines}
        </section>
      ) : null}
    </>
  );
}

function BoundFormField({
  surface,
  field,
  relation,
  rail = false,
}: {
  surface: FormViewSurface;
  field: FieldDescriptor;
  relation: RelationFieldInfo | undefined;
  rail?: boolean;
}): React.ReactElement {
  const values = useWatch({ control: surface.form.control });
  const value = get(values, field.name);
  const readOnly = surface.fieldReadOnly(field);
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
          selectedOption={selectedOption}
          value={value}
          row={values}
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

/** Rail fields use the same form controller and widget binding as body fields. */
export function FormViewRail({ surface }: { surface: FormViewSurface }): React.ReactElement | null {
  if (surface.railGroups.length === 0) return null;
  return <aside className="min-w-0 border-t border-border-subtle pt-5 @min-[52rem]:border-l @min-[52rem]:border-t-0 @min-[52rem]:pl-5 @min-[52rem]:pt-0">
    <div className="grid gap-7">
      {surface.railGroups.map((group) => <section key={group.id} className="grid gap-3">
        <SectionHeading label={group.label} summary={group.summary} hint={group.hint} audience={group.audience} />
        {(group.fields ?? []).map(({ field }) => <BoundFormField key={field.name} surface={surface} field={field}
          relation={surface.relationByField.get(field.name)} rail />)}
        {group.content}
      </section>)}
    </div>
  </aside>;
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

function FormSectionTabs({
  sections,
  renderField,
  control,
  requestedFocusPath,
  lineField,
}: {
  sections: readonly FormSectionModel[];
  renderField: (field: FieldDescriptor) => React.ReactNode;
  control: Control<FormValues>;
  requestedFocusPath: string | null;
  lineField: string | null;
}): React.ReactElement {
  const [active, setActive] = React.useState(sections[0]?.key);
  const trackedFields = sections.flatMap((section) => section.fields.map((field) => field.name));
  if (lineField) trackedFields.push(lineField);
  const { errors, submitCount } = useFormState({ control, name: trackedFields });
  const handledSubmitCount = React.useRef(submitCount);
  React.useEffect(() => {
    const focus = requestedFocusPath;
    if (focus) {
      const target = sections.find((section) =>
        section.fields.some((field) => focus === field.name || focus.startsWith(`${field.name}.`))
        || (section.key === "editable-lines" && lineField && (focus === lineField || focus.startsWith(`${lineField}.`))),
      );
      if (target) setActive(target.key);
      return;
    }
    if (handledSubmitCount.current === submitCount) return;
    handledSubmitCount.current = submitCount;
    const errored = sections.find((section) =>
      section.fields.some((field) => get(errors, field.name) !== undefined)
      || (section.key === "editable-lines" && lineField && get(errors, lineField) !== undefined),
    );
    if (errored) setActive(errored.key);
  }, [errors, lineField, requestedFocusPath, sections, submitCount]);
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
          <SectionHeading label={section.label} count={section.badge} className="mb-3" />
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
  rail = false,
  relation,
  selectedOption,
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
  rail?: boolean;
  relation?: RelationFieldInfo;
  selectedOption?: RelationOption;
  value: unknown;
  row: FormValues;
  readOnly?: boolean;
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
      <FieldLabel className={cn(FIELD_LABEL_CLASS, rail && "mb-0 min-h-8 normal-case tracking-normal")}
        title={developerTitle(field.name, field.widget)}>
        {field.label ?? field.name}
        <RecordFieldMarkButton field={field.name} label={field.label} />
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
            filters={field.filters}
            selectedOption={selectedOption}
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
      </div>
      <FieldFooter description={field.description} errors={displayedMessages} />
    </FieldRoot>
  );
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
