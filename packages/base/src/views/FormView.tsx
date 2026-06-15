import * as React from "react";
import { useForm, useStore } from "@tanstack/react-form";
import { useBlocker } from "@tanstack/react-router";
import {
  relayGlobalIdSuffix,
  useFormOverride,
  useResourceMutation,
  useResourceRecord,
  useModelMetadata,
  useSchemaFieldMetadata,
  useSlot,
  validationErrorsFromError,
  type ModelMetadata,
  type Row,
} from "@angee/sdk";

import { Button } from "../ui/button";
import { ErrorBanner } from "../fragments/ErrorBanner";
import { useConfirm } from "../feedback";
import {
  FieldDescription,
  FieldLabel,
  FieldRoot,
} from "../ui/field";
import { FormGrid } from "../ui/form-layout";
import { SectionEyebrow } from "../ui/section-eyebrow";
import { Input } from "../ui/input";
import { Spinner } from "../ui/spinner";
import { Tabs } from "../ui/tabs";
import { ControlBand } from "../shell/ControlBand";
import { cn } from "../lib/cn";
import { SlotOutlet } from "../lib/slot-outlet";
import {
  useResolvedWidget,
  type WidgetDefinition,
  type WidgetField,
} from "../widgets";
import {
  fieldWidgetId,
  isRelationIdField,
  pageChildren,
  pageElementProps,
  parsePageActions,
  parsePageFields,
  parsePageGroups,
  type ActionDescriptor,
  type FieldDescriptor,
  type FieldProps,
  type GroupDescriptor,
  type GroupProps,
  type PageFieldKind,
} from "./page";
import {
  fieldsWithMetadataDefaults,
  relationFieldInfo,
  type RelationFieldInfo,
} from "./model-metadata-defaults";
import {
  RecordActionBar,
  type RecordDeleteAction,
} from "./RecordActionBar";
import { RelationFieldWidget } from "./RelationFieldWidget";
import { useBaseT } from "../i18n";

export type FieldKind = PageFieldKind;
export type FormField = FieldDescriptor;

/**
 * The context a saved-record panel renders against: the open record id and a
 * `reload` that refetches the form. Shared by `recordExtras` (below the form) and
 * each `recordTabs` panel (beside the "Overview" tab) so a panel doing out-of-band
 * writes can refresh the form's fields after.
 */
export interface RecordPanelContext {
  recordId: string;
  reload: () => void;
}

/** One tab beside the form's leading "Overview" tab, for a saved record. */
export interface RecordTabDescriptor {
  /** Stable tab id, also used as the panel value. */
  id: string;
  /** Tab label. */
  label: React.ReactNode;
  /** Optional leading icon. */
  icon?: React.ReactNode;
  /** Panel content rendered outside the `<form>`. */
  render: (context: RecordPanelContext) => React.ReactNode;
}

/** Value of the form body's leading tab, shown when `recordTabs` is set. */
const OVERVIEW_TAB_ID = "overview";

export interface FormViewProps {
  /** Model label rendered by this form, e.g. `"notes.Note"`. */
  model: string;
  /** Record id to edit; `null` or `undefined` renders a create form. */
  id?: string | null;
  /** Fields rendered by the record form. */
  fields?: readonly FieldDescriptor[];
  /** Grouped sections rendered by the record form. */
  groups?: readonly GroupDescriptor[];
  /** Field and group element declarations parsed when `fields`/`groups` are omitted. */
  children?: React.ReactNode;
  /** Record actions rendered in the toolbar; parsed from children when omitted. */
  actions?: readonly ActionDescriptor[];
  /** Extra fields returned after save and selected while editing. */
  returning?: readonly string[];
  /** Initial values merged into create forms after widget empty defaults. */
  defaultValues?: Record<string, unknown>;
  /** Called after a successful save. */
  onSaved?: (row: Row) => void;
  /** Label used for the submit button. */
  submitLabel?: React.ReactNode;
  /** Left-side record commands the host renders before dirty Save/Discard actions. */
  toolbarStart?: React.ReactNode;
  /** Right-side record chrome the host renders after the toolbar spacer. */
  toolbar?: React.ReactNode;
  /**
   * Custom content rendered below the form for a *saved* record (never on
   * create). Receives the open record id and a `reload` that refetches the form
   * — so a panel doing out-of-band writes (e.g. operator provisioning) can refresh
   * the form's fields after. Rendered outside the `<form>`, so its own buttons
   * never submit the edit form.
   */
  recordExtras?: (context: RecordPanelContext) => React.ReactNode;
  /**
   * Tabs for a *saved* record (never on create). When set, the form body (field
   * sections + body field) becomes the leading "Overview" tab and each descriptor
   * adds a sibling panel — rendered outside the `<form>` with the same
   * `{ recordId, reload }` context as `recordExtras`, so a panel's own buttons
   * never submit the edit form. Omitted/empty renders the plain form.
   */
  recordTabs?: readonly RecordTabDescriptor[];
  /** Optional delete command folded into the record action menu. */
  deleteAction?: RecordDeleteAction;
  /** Class name applied to the form root. */
  className?: string;
}

/**
 * Slot for record-level chrome (e.g. star/share/follow) rendered in the form
 * toolbar of a saved record. Base ships no product affordances here — a host or
 * addon contributes them at build time via `slots: [{ slot:
 * FORM_VIEW_RECORD_CHROME_SLOT, id, content }]`. Contributions render in their
 * merged order on a saved record only (not while creating).
 */
export const FORM_VIEW_RECORD_CHROME_SLOT = "form-view.record-chrome";

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
// The form's centered content column — shared by the form body and the
// `recordExtras`/`recordTabs` panels around it so the column width lives in one place.
const FORM_COLUMN_CLASS = "mx-auto w-full max-w-[1100px] px-6 sm:px-8";

export function FormView({
  model,
  id,
  fields,
  groups,
  children,
  actions,
  returning,
  defaultValues,
  onSaved,
  submitLabel,
  toolbarStart,
  toolbar,
  recordExtras,
  recordTabs,
  deleteAction,
  className,
}: FormViewProps): React.ReactElement {
  const t = useBaseT();
  const [activeRecordTab, setActiveRecordTab] = React.useState(OVERVIEW_TAB_ID);
  const hasFieldChildren = hasPageField(children);
  const hasGroupChildren = hasDirectPageElement(children, "group");
  if (
    (fields !== undefined && hasFieldChildren) ||
    (groups !== undefined && hasGroupChildren)
  ) {
    throw new Error(
      "FormView cannot mix the fields/groups props with element children.",
    );
  }
  const childFields = parsePageFields(children);
  const childGroups = parsePageGroups(children);
  const childActions = parsePageActions(children);
  const modelMetadata = useModelMetadata(model);
  const schemaMetadata = useSchemaFieldMetadata();
  const formOverride = useFormOverride(model);
  // Host/addon-contributed record chrome (star/share/…); base ships none.
  const recordChrome = useSlot(FORM_VIEW_RECORD_CHROME_SLOT);
  const isCreate = id == null;
  // An addon may register a declarative create form for a model (composed into the
  // runtime). On create it replaces the declared/metadata fields, so DataPage "New"
  // and the relation-picker inline create render the same form for that model. A
  // malformed registration (not an element) reads as no override, not a blank form.
  const overrideNode =
    isCreate && React.isValidElement(formOverride) ? formOverride : null;
  const declaredFields =
    overrideNode != null ? parsePageFields(overrideNode) : fields ?? childFields;
  const declaredGroups =
    overrideNode != null ? parsePageGroups(overrideNode) : groups ?? childGroups;
  const declaredActions =
    overrideNode != null ? parsePageActions(overrideNode) : actions ?? childActions;
  const resolvedFields = React.useMemo(
    () =>
      withModeLockedFields(
        fieldsWithMetadataDefaults(declaredFields, modelMetadata),
        isCreate,
      ),
    [declaredFields, modelMetadata, isCreate],
  );
  const resolvedGroups = React.useMemo(
    () =>
      declaredGroups.map((group) => ({
        ...group,
        fields: withModeLockedFields(
          fieldsWithMetadataDefaults(group.fields, modelMetadata),
          isCreate,
        ),
      })),
    [declaredGroups, modelMetadata, isCreate],
  );
  const formFields = React.useMemo(
    () => flattenedFormFields(resolvedFields, resolvedGroups),
    [resolvedFields, resolvedGroups],
  );
  const fieldByName = React.useMemo(
    () => new Map(formFields.map((field) => [field.name, field])),
    [formFields],
  );
  // When any field is conditional, the body subscribes to live form values so a
  // discriminator change (e.g. a `kind` select) re-evaluates `showWhen`.
  const hasConditionalFields = React.useMemo(
    () => formFields.some((field) => field.showWhen),
    [formFields],
  );
  // Object-relation fields with no explicit options auto-wire to the searchable
  // creatable picker; the SDL resolves each one's model, display field, and
  // whether it can be created inline.
  const relationByField = React.useMemo(() => {
    const map = new Map<string, RelationFieldInfo>();
    for (const field of formFields) {
      if (field.options) continue;
      const info = relationFieldInfo(field.name, modelMetadata, schemaMetadata);
      if (info) map.set(field.name, info);
    }
    return map;
  }, [formFields, modelMetadata, schemaMetadata]);
  const selection = React.useMemo(() => {
    const paths = new Set<string>(["id"]);
    for (const field of formFields) {
      // Project only fields the result type exposes. A write-only input
      // (password, apiKey, secret) is declared on the form but absent from the
      // SDL read type; selecting it makes the whole detail/return query invalid
      // and the record loads as null. The SDL metadata owns which fields are
      // readable — skip a declared field once metadata is loaded and lacks it.
      if (modelMetadata && !modelMetadata.fields[field.name]) continue;
      addFieldSelection(paths, field);
    }
    for (const extra of returning ?? []) paths.add(extra);
    return [...paths];
  }, [formFields, modelMetadata, returning]);

  const { record, fetching: loading, refetch } = useResourceRecord(
    model,
    id ?? null,
    {
      fields: selection,
      enabled: !isCreate,
    },
  );
  const [mutate, mutation] = useResourceMutation(
    model,
    isCreate ? "create" : "update",
    { fields: selection },
  );
  const emptyValues = React.useMemo(
    () => emptyDraft(formFields, defaultValues),
    [defaultValues, formFields],
  );
  const formReadOnly = React.useMemo(
    () => formFields.length > 0 && formFields.every((field) => field.readOnly),
    [formFields],
  );
  // `useForm` re-seeds an untouched form whenever `defaultValues` deep-changes.
  // Source it from this stable baseline ref (reassigned only on record seed,
  // post-save reset, and create reset) so a post-save re-render carrying new
  // field-descriptor identities can't re-seed and blank the just-saved values.
  const baselineValuesRef = React.useRef<Values>(emptyValues);
  const [saveError, setSaveError] = React.useState<string | null>(null);
  const [serverFieldErrors, setServerFieldErrors] = React.useState<
    Record<string, readonly string[]>
  >({});
  const clearServerFieldError = React.useCallback((name: string) => {
    setServerFieldErrors((prev) => {
      if (!prev[name]) return prev;
      const { [name]: _removed, ...rest } = prev;
      return rest;
    });
  }, []);
  // Fields the create input requires (non-null, no default) that this form
  // renders editably — validated client-side so a missing one is flagged inline
  // instead of failing as a GraphQL "required type was not provided" coercion error.
  const requiredFieldNames = React.useMemo(() => {
    const required = new Set(modelMetadata?.rootFields?.requiredCreateFields ?? []);
    return formFields
      .filter((field) => required.has(field.name) && !field.readOnly)
      .map((field) => field.name);
  }, [formFields, modelMetadata]);
  const form = useForm({
    defaultValues: baselineValuesRef.current,
    onSubmit: async ({ value }) => {
      setSaveError(null);
      setServerFieldErrors({});
      if (isCreate) {
        const missing = requiredFieldNames.filter(
          (name) =>
            isEmptyFieldValue(value[name]) &&
            isFieldVisible(fieldByName.get(name) ?? { name }, value),
        );
        if (missing.length > 0) {
          setServerFieldErrors(
            Object.fromEntries(
              missing.map((name) => [name, ["This field is required."]]),
            ),
          );
          setSaveError("Please fill in the required fields.");
          return;
        }
      }
      const data = mutationData(value, formFields, {
        baseline: baselineValuesRef.current,
        id,
        isCreate,
      });
      try {
        const saved = await mutate({ data });
        if (saved) {
          const savedValues = recordToValues(saved, formFields);
          baselineValuesRef.current = savedValues;
          form.reset(savedValues);
          onSaved?.(saved);
        }
      } catch (error) {
        // Distribute Django field validation under each field; keep the banner
        // for form-level (non-field) messages, or a prompt to fix the fields.
        const { fieldErrors, formErrors } = validationErrorsFromError(error);
        setServerFieldErrors(fieldErrors);
        setSaveError(
          formErrors.length > 0
            ? formErrors.join(" ")
            : Object.keys(fieldErrors).length > 0
              ? "Please fix the highlighted fields."
              : "Could not save record.",
        );
      }
    },
  });
  const formIsDirty = useStore(form.store, (state) => state.isDirty);
  useUnsavedChangesNavigationGuard({
    isDirty: formIsDirty,
    readOnly: formReadOnly,
  });

  // Apply a record action's field patch through the form's own update mutation
  // and re-seed from the result — the same path as a save, so the form shows the
  // new values without a refetch. Reused by `set` actions and the action context.
  const applyPatch = React.useCallback(
    async (patch: Record<string, unknown>): Promise<Row | null> => {
      if (id == null) {
        throw new Error("No open record to update.");
      }
      const saved = await mutate({ data: { id, ...patch } });
      if (saved) {
        const savedValues = recordToValues(saved, formFields);
        baselineValuesRef.current = savedValues;
        form.reset(savedValues);
        setSaveError(null);
        setServerFieldErrors({});
      }
      return saved;
    },
    [form, formFields, id, mutate],
  );

  // A custom `run` action mutates server state we can't predict; re-pull the
  // record (network-only, so a fresh object always lands) and let the seed
  // effect re-seed off the new reference.
  const reload = React.useCallback(() => {
    refetch();
  }, [refetch]);

  const seededIdRef = React.useRef<string | null>(null);
  const seededRecordRef = React.useRef<Row | null>(null);
  React.useEffect(() => {
    setSaveError(null);
    setServerFieldErrors({});
    setActiveRecordTab(OVERVIEW_TAB_ID);
  }, [model, id]);

  React.useEffect(() => {
    if (isCreate) {
      if (seededIdRef.current !== null) {
        seededIdRef.current = null;
        seededRecordRef.current = null;
        baselineValuesRef.current = emptyValues;
        form.reset(emptyValues);
        setSaveError(null);
      }
      return;
    }
    const recordId = typeof record?.id === "string" ? record.id : null;
    // Re-seed on a new record id, or when the record *object reference* changes
    // for the same id — a refetch landed with fresh server state (e.g. after a
    // record action). Keying off the reference (not a manual flag) means a stale
    // intervening render carrying the same record can't consume the re-seed.
    if (
      record &&
      recordId &&
      (seededIdRef.current !== recordId || seededRecordRef.current !== record)
    ) {
      seededIdRef.current = recordId;
      seededRecordRef.current = record;
      const recordValues = recordToValues(record, formFields);
      baselineValuesRef.current = recordValues;
      form.reset(recordValues);
      setSaveError(null);
    }
  }, [emptyValues, formFields, isCreate, record, form]);

  const titleField = titleFieldFor(formFields, modelMetadata);
  const statusField = formFields.find(
    (field) => fieldWidgetId(field) === "statusbar" && !field.showWhen,
  );
  const bodyField = React.useMemo(
    () => bodyFieldFor(formFields, titleField, statusField),
    [formFields, statusField, titleField],
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

  const renderField = (field: FieldDescriptor): React.ReactNode => (
    <form.Field key={field.name} name={field.name}>
      {(api) => (
        <BoundFieldRow
          field={field}
          relation={relationByField.get(field.name)}
          value={api.state.value}
          errors={api.state.meta.errors}
          serverMessages={serverFieldErrors[field.name]}
          onChange={(next) => {
            clearServerFieldError(field.name);
            api.handleChange(next);
          }}
        />
      )}
    </form.Field>
  );

  const recordPanelContext: RecordPanelContext | null =
    !isCreate && id != null ? { recordId: id, reload } : null;
  const recordTabList = recordTabs ?? [];
  const tabbed = recordPanelContext != null && recordTabList.length > 0;

  // The form body: field sections then the prominent body field. Shown inline on a
  // plain form, or wrapped as the leading "Overview" tab when `recordTabs` is set.
  const overviewBody = (
    <>
      <div className="grid gap-6">
        {hasConditionalFields ? (
          <form.Subscribe selector={(state) => state.values}>
            {(values) =>
              visibleSections(sections, values as Values).map((section) => (
                <FormSection
                  key={section.key}
                  section={section}
                  renderField={renderField}
                />
              ))
            }
          </form.Subscribe>
        ) : (
          sections.map((section) => (
            <FormSection
              key={section.key}
              section={section}
              renderField={renderField}
            />
          ))
        )}
      </div>

      {bodyField ? (
        <section className="grid gap-2">
          {bodyField.label ? (
            <SectionEyebrow as="span">{bodyField.label}</SectionEyebrow>
          ) : null}
          <form.Field name={bodyField.name}>
            {(api) => (
              <BodyFieldControl
                field={bodyField}
                value={api.state.value}
                errors={api.state.meta.errors}
                serverMessages={serverFieldErrors[bodyField.name]}
                onChange={(next) => {
                  clearServerFieldError(bodyField.name);
                  api.handleChange(next);
                }}
              />
            )}
          </form.Field>
        </section>
      ) : null}
    </>
  );

  // Stacked below the form (no tabs). Rendered outside the `<form>` so its buttons
  // never submit; same context as a tab panel.
  const recordExtrasPanel =
    recordPanelContext && recordExtras ? (
      <div className={cn(FORM_COLUMN_CLASS, "pb-12")}>
        {recordExtras(recordPanelContext)}
      </div>
    ) : null;

  const formElement = (
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
          const isSaving = mutation.fetching || state.isSubmitting;
          // The toolbar is the single home for record commands: host-provided
          // left commands (delete) and Save/Discard, the record's domain actions,
          // then record chrome (star/share) and host-provided right chrome
          // (pager/view-switcher). Under a shell the band portals out of the
          // <form>, so Save submits via handleSubmit(), not native type="submit".
          return (
            <ControlBand className={state.isDirty ? "bg-brand-soft" : undefined}>
              <div className="flex min-w-0 items-center gap-2">
                {toolbarStart}
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
                {declaredActions.length > 0 || deleteAction !== undefined ? (
                  <RecordActionBar
                    record={record ?? null}
                    actions={declaredActions}
                    applyPatch={applyPatch}
                    reload={reload}
                    deleteAction={deleteAction}
                  />
                ) : null}
              </div>
              <div className="min-w-2 flex-1" />
              <div className="flex min-w-0 items-center gap-2">
                {!isCreate ? <SlotOutlet entries={recordChrome} /> : null}
                {toolbar}
              </div>
            </ControlBand>
          );
        }}
      </form.Subscribe>
      <div
        className={cn(
          FORM_COLUMN_CLASS,
          "flex flex-col gap-6 pt-6",
          // On a non-Overview tab the body is hidden, so drop the tall bottom
          // padding — the active tab panel below owns its own spacing.
          tabbed && activeRecordTab !== OVERVIEW_TAB_ID ? "pb-4" : "pb-12",
        )}
      >
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
                        onChange={(event) => {
                          clearServerFieldError(titleField.name);
                          api.handleChange(event.currentTarget.value);
                        }}
                      />
                    )
                  )}
                </form.Field>
              ) : (
                <h1 className="truncate text-28 font-semibold leading-9 text-fg">
                  Record
                </h1>
              )}
              {/* The title field renders in the header (no FieldRoot), so its
                  server validation error is surfaced here rather than inline. */}
              {titleField && serverFieldErrors[titleField.name] ? (
                <p className="mt-1 text-xs leading-5 text-danger-text">
                  {serverFieldErrors[titleField.name]?.join(", ")}
                </p>
              ) : null}
              <RecordSubtitle loading={loading} parts={subtitleParts} />
            </div>
            {statusField ? (
              <div className="flex min-w-0 shrink-0 flex-wrap items-center justify-end gap-3 max-[900px]:w-full">
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
              </div>
            ) : null}
          </div>

        </header>

        <ErrorBanner description={saveError} title={t("form.saveFailed")} />

        {tabbed ? (
          <>
            <Tabs.List>
              <Tabs.Tab value={OVERVIEW_TAB_ID}>{t("form.tabOverview")}</Tabs.Tab>
              {recordTabList.map((tab) => (
                <Tabs.Tab key={tab.id} value={tab.id} icon={tab.icon}>
                  {tab.label}
                </Tabs.Tab>
              ))}
            </Tabs.List>
            <Tabs.Panel value={OVERVIEW_TAB_ID} keepMounted className="grid gap-6 pt-0">
              {overviewBody}
            </Tabs.Panel>
          </>
        ) : (
          overviewBody
        )}
      </div>
    </form>
  );

  if (!tabbed) {
    return (
      <>
        {formElement}
        {recordExtrasPanel}
      </>
    );
  }

  // Tabs span the form (Overview) and the sibling panels rendered outside it; the
  // shared root wires them through context regardless of DOM nesting.
  return (
    <Tabs value={activeRecordTab} onValueChange={setActiveRecordTab} variant="card">
      {formElement}
      {recordTabList.map((tab) => (
        <Tabs.Panel
          key={tab.id}
          value={tab.id}
          keepMounted
          className={cn(FORM_COLUMN_CLASS, "pb-12")}
        >
          {recordPanelContext ? tab.render(recordPanelContext) : null}
        </Tabs.Panel>
      ))}
      {recordExtrasPanel}
    </Tabs>
  );
}

function hasDirectPageElement(
  children: React.ReactNode,
  kind: "action" | "group",
): boolean {
  return pageChildren(children).some((child) =>
    Boolean(pageElementProps<unknown>(child, kind)),
  );
}

function hasPageField(children: React.ReactNode): boolean {
  for (const child of pageChildren(children)) {
    if (pageElementProps<FieldProps>(child, "field")) return true;
    const group = pageElementProps<GroupProps>(child, "group");
    if (group && hasPageField(group.children)) return true;
  }
  return false;
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
  const widget = useResolvedWidget(fieldWidgetId(field)) ?? fallbackWidget();
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

function BoundFieldRow({
  field,
  relation,
  value,
  errors,
  serverMessages,
  onChange,
}: {
  field: FieldDescriptor;
  relation?: RelationFieldInfo;
  value: unknown;
  errors: readonly unknown[];
  serverMessages?: readonly string[];
  onChange: (value: unknown) => void;
}): React.ReactElement {
  const readOnly = Boolean(field.readOnly);
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
          readOnly ? READONLY_FIELD_CONTROL_CLASS : EDITABLE_FIELD_CONTROL_CLASS,
        )}
      >
        {relation ? (
          <RelationFieldWidget
            value={typeof value === "string" ? value : null}
            onChange={onChange}
            readOnly={readOnly}
            relation={relation}
            aria-label={fieldAriaLabel(field)}
          />
        ) : (
          <FieldWidget
            field={field}
            value={value}
            readOnly={field.readOnly}
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
  errors,
  serverMessages,
  onChange,
}: {
  field: FieldDescriptor;
  value: unknown;
  errors: readonly unknown[];
  serverMessages?: readonly string[];
  onChange: (value: unknown) => void;
}): React.ReactElement {
  const messages = [...fieldErrorMessages(errors), ...(serverMessages ?? [])];
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
  // Render messages directly rather than base-ui's `Field.Error`: that only
  // shows when the control's own validity is invalid, which custom widgets never
  // set — FormView owns the messages (server validation + form state) here.
  return (
    <>
      {description ? <FieldDescription>{description}</FieldDescription> : null}
      {errors.length > 0 ? (
        <p className="text-xs leading-5 text-danger-text">{errors.join(", ")}</p>
      ) : null}
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
  metadata: ModelMetadata | null,
): FieldDescriptor | undefined {
  // Title/body/status render unconditionally outside the filtered grid; a
  // `showWhen` field belongs in the grid so its predicate is honored.
  const stable = fields.filter((field) => !field.showWhen);
  return stable.find((field) => field.title) ??
    stable.find((field) => field.name === metadata?.recordRepresentation) ??
    stable.find((field) => field.name === "title");
}

function bodyFieldFor(
  fields: readonly FieldDescriptor[],
  titleField: FieldDescriptor | undefined,
  statusField: FieldDescriptor | undefined,
): FieldDescriptor | undefined {
  const candidates = fields.filter(
    (field) =>
      // `body={false}` opts a field out of the body slot — e.g. a `description`
      // field that should render as a normal field, not the prominent body.
      field.body !== false &&
      !field.showWhen &&
      field.name !== titleField?.name &&
      field.name !== statusField?.name,
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
  // `fieldWidgetId` already returns `kind` when no `widget` is set, so a bare
  // `kind:"textarea"` resolves to the `textarea` id — a separate `field.kind`
  // read would only (wrongly) fire when an explicit widget overrides the kind.
  const id = fieldWidgetId(field);
  return (
    id === "textarea" ||
    id === "markdown" ||
    id === "markdown.editor" ||
    id === "markdown.preview"
  );
}

function titleText(value: unknown): string {
  const text = String(value ?? "").trim();
  return text || "Untitled";
}

function addFieldSelection(
  paths: Set<string>,
  field: FieldDescriptor,
): void {
  if (isRelationIdField(field)) {
    paths.add(`${field.name}.id`);
    return;
  }
  paths.add(field.name);
}

/**
 * Lock a field for the wrong mode: a `createOnly` field is read-only on an edit
 * (an immutable key, or a create-time input the patch type rejects); an
 * `editOnly` field is read-only on a create (a field the create input omits).
 * Read-only fields are rendered uneditable and `mutationData` never sends them.
 * No effect on plain fields.
 */
function withModeLockedFields(
  fields: readonly FieldDescriptor[],
  isCreate: boolean,
): readonly FieldDescriptor[] {
  return fields.map((field) => {
    const locked = isCreate ? field.editOnly : field.createOnly;
    return locked && !field.readOnly ? { ...field, readOnly: true } : field;
  });
}

function flattenedFormFields(
  fields: readonly FieldDescriptor[],
  groups: readonly GroupDescriptor[],
): readonly FieldDescriptor[] {
  const seen = new Set<string>();
  const flattened: FieldDescriptor[] = [];
  for (const field of fields) addFormField(flattened, seen, field);
  for (const group of groups) {
    for (const field of group.fields) addFormField(flattened, seen, field);
  }
  return flattened;
}

function addFormField(
  fields: FieldDescriptor[],
  seen: Set<string>,
  field: FieldDescriptor,
): void {
  if (seen.has(field.name)) return;
  seen.add(field.name);
  fields.push(field);
}

function emptyDraft(
  fields: readonly FieldDescriptor[],
  defaultValues?: Record<string, unknown>,
): Values {
  const draft: Values = {};
  for (const field of fields) {
    draft[field.name] = Object.hasOwn(defaultValues ?? {}, field.name)
      ? defaultValues?.[field.name]
      : emptyValue(field);
  }
  return draft;
}

function recordToValues(record: Row, fields: readonly FieldDescriptor[]): Values {
  const values: Values = {};
  for (const field of fields) {
    values[field.name] = recordFieldValue(record, field) ?? emptyValue(field);
  }
  return values;
}

function recordFieldValue(record: Row, field: FieldDescriptor): unknown {
  const value = record[field.name];
  if (!isRelationIdField(field)) return value;
  if (typeof value === "string") return value;
  if (isRecord(value) && typeof value.id === "string") return value.id;
  return value;
}

/** Whether a field is shown for the current values — false hides and never submits it. */
function isFieldVisible(field: FieldDescriptor, values: Values): boolean {
  return !field.showWhen || field.showWhen(values);
}

/** Drop fields whose `showWhen` predicate fails; an emptied section renders nothing. */
function visibleSections(
  sections: readonly FormSectionModel[],
  values: Values,
): readonly FormSectionModel[] {
  return sections.map((section) => ({
    ...section,
    fields: section.fields.filter((field) => isFieldVisible(field, values)),
  }));
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
    // A field hidden by its `showWhen` predicate is not part of the record.
    if (!isFieldVisible(field, values)) continue;
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
  if (isNullableScalarWidget(field)) return null;
  if (field.widget === "tagInput") return [];
  if (field.kind === "switch" || field.widget === "switch") return false;
  // An empty JSON field is an empty object, not the JSON string "" — the latter
  // is stored verbatim and breaks downstream `config.get(...)` reads.
  if (fieldWidgetId(field) === "json") return {};
  return "";
}

/** A value counts as unfilled for required validation: null, empty string, or empty list. */
function isEmptyFieldValue(value: unknown): boolean {
  if (value == null) return true;
  if (typeof value === "string") return value.trim() === "";
  if (Array.isArray(value)) return value.length === 0;
  return false;
}

function isNullableScalarWidget(field: FieldDescriptor): boolean {
  const id = fieldWidgetId(field);
  return id === "date" || id === "datetime";
}

function hasOptionValue(field: FieldDescriptor): boolean {
  return Boolean(
    field.options &&
      (field.widget === "select" ||
        field.widget === "many2one" ||
        field.widget === "statusbar" ||
        field.kind === "select" ||
        field.kind === "selection"),
  );
}

function isUnselectedOption(field: FieldDescriptor, value: unknown): boolean {
  // An empty relation id is also "unselected" — auto-wired relations carry no
  // inline `options`, so `hasOptionValue` alone would let `""` through as a FK.
  return value === "" && (hasOptionValue(field) || isRelationIdField(field));
}

function valuesEqual(left: unknown, right: unknown): boolean {
  if (Object.is(left, right)) return true;
  if (!Array.isArray(left) || !Array.isArray(right)) return false;
  return (
    left.length === right.length &&
    left.every((item, index) => valuesEqual(item, right[index]))
  );
}

function isRecord(value: unknown): value is Row {
  return Boolean(value) && typeof value === "object";
}

function fieldAriaLabel(field: FieldDescriptor): string {
  return typeof field.label === "string" ? field.label : field.name;
}

function gridFieldClass(field: FieldDescriptor): string | undefined {
  return fieldWidgetId(field) === "tagInput" ? FULL_FIELD_CLASS : undefined;
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
  if (recordId !== undefined) parts.push(recordIdLabel(String(recordId)));
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

/**
 * Prefer the human-facing public id in the subtitle. A relay global id encodes
 * `Type:publicId`, so the relay codec decodes it to that suffix (e.g. the sqid);
 * otherwise fall back to a short slice of whatever identifier the record carries.
 */
function recordIdLabel(value: string): string {
  return relayGlobalIdSuffix(value) ?? shortRecordId(value);
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
