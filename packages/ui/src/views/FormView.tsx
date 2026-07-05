import * as React from "react";
import {
  lineReadSelectionPaths,
  refineResourceName,
  recordSubtitleFields,
  rowPublicId,
  type RecordSubtitleFields,
  type Row,
} from "@angee/metadata";
import {
  useInvalidate,
  type BaseKey,
  type BaseRecord,
  type HttpError,
  } from "@refinedev/core";
import { useForm as useRefineForm } from "@refinedev/react-hook-form";
import { Controller,
  useWatch,
  type Control } from "react-hook-form";
import { useBlocker } from "@tanstack/react-router";
import {
  refineFieldsFromPaths,
  useAngeeResourceSave,
  } from "@angee/refine";
import {
  publicIdLabel,
} from "@angee/metadata";
import {
  useSchemaFieldMetadata,
  type ModelMetadata,
} from "@angee/metadata";
import {
  useFormOverride,
  useSlot,
  } from "../runtime";
import {
  useModelMetadata,
} from "@angee/metadata";

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
import { textRoleVariants } from "../ui/text";
import { ControlBand } from "../layouts/ControlBand";
import { cn } from "../lib/cn";
import { SlotOutlet } from "../lib/slot-outlet";
import {
  RecordChromeProvider,
  type RecordChromeContext,
} from "./record-chrome-context";
import { EditableLines } from "./EditableLines";
import {
  diffLines,
  lineDiffConfig,
  recordLinesToRows,
  type LineDiff,
} from "./editable-lines";
import { useSaveOperation } from "./resource-operations";
import {
  validationErrorsFromError,
  type ValidationErrors,
} from "./validation-errors";
import {
  slugify,
} from "../widgets";
import { canonicalOptionValue, relationValueId } from "../widgets/types";
import { dateFromUnknown, formatDate } from "../widgets/date-format";
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
import { relationSelectedOption } from "./relation-options";
import type { RelationOption } from "../widgets/RelationField";
import { useUiT, type UiTranslate } from "../i18n";
import { FieldDescriptorControl } from "./field-descriptor-control";

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

export interface RecordToolbarContext {
  recordId: string | null;
  record: Row | null;
  patchRecord: (patch: Record<string, unknown>) => void;
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
  /**
   * Keep the panel mounted while another tab is active. Off by default so a panel that
   * does eager work on mount — a socket/subscription, its own fetch, a token mint (e.g.
   * the agent Chat tab opens an ACP WebSocket) — stays inert until its tab is first
   * selected. Opt in only for a panel whose internal state must survive tab switches.
   */
  keepMounted?: boolean;
}

/** Value of the form body's leading tab, shown when `recordTabs` is set. */
const OVERVIEW_TAB_ID = "overview";

export interface FormViewProps {
  /** Model label rendered by this form, e.g. `"notes.Note"`. */
  resource: string;
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
  /**
   * List-scope create seed merged into create forms after widget empty defaults —
   * the page-level owner (`ResourceList` passes its `createDefaults` here to seed
   * rows matching the active filter/facet). For a *fixed per-field* create default
   * a form owns, prefer the field's own `Field.defaultValue`; this prop stays the
   * owner of list-scope seeding. It wins over `Field.defaultValue` where both name
   * the same field, since a caller passing it names the invoking list context.
   */
  defaultValues?: Record<string, unknown>;
  /** Called after a successful save. */
  onSaved?: (row: Row) => void;
  /**
   * Custom save owner for resources that do not expose the stock Hasura
   * create/update root. Receives the same normalized mutation payload the stock
   * refine submit path would send, and returns the saved row used to re-seed the
   * form.
   */
  submit?: FormSubmit;
  /** Label used for the submit button. */
  submitLabel?: React.ReactNode;
  /**
   * Left-side record commands the host renders before dirty Save/Discard actions.
   * A function receives the loaded record so commands can hide or disable against
   * server state while keeping the form toolbar as the layout owner.
   */
  toolbarStart?: React.ReactNode | ((context: RecordToolbarContext) => React.ReactNode);
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
  /** Hide the optional delete command unless the loaded record matches. */
  deleteVisibleWhen?: (record: Row) => boolean;
  /**
   * Section layout. `"stacked"` (default) renders each `<Group>` as a labelled
   * section stacked down the form. `"tabs"` renders each *labelled* group as a tab
   * panel via the shared `<Tabs>` primitive; the title/body/status and any
   * ungrouped fields stay above the tab strip. Opt a long form in per form
   * (`<Form layout="tabs">`); existing stacked forms are unaffected.
   */
  layout?: "stacked" | "tabs";
  /** Class name applied to the form root. */
  className?: string;
}

export interface FormSubmitContext {
  resource: string;
  id: string | null;
  isCreate: boolean;
  record: Row | null;
  /**
   * The diffed document lines (F6) when the resource declares editable lines and
   * the form is dirty, else `null`. A custom `submit` owner for a lines resource
   * persists them; the stock save path routes through `<resource>_save` directly.
   */
  lines: LineDiff | null;
}

export type FormSubmit = (
  data: Record<string, unknown>,
  context: FormSubmitContext,
) => Row | null | undefined | Promise<Row | null | undefined>;

/**
 * Slot for record-level chrome (e.g. star/share/follow) rendered in the form
 * toolbar of a saved record. Base ships no product affordances here — a host or
 * addon contributes them at build time via `slots: [{ slot:
 * FORM_VIEW_RECORD_CHROME_SLOT, id, content }]`. Contributions render in their
 * merged order on a saved record only (not while creating).
 */
export const FORM_VIEW_RECORD_CHROME_SLOT = "form-view.record-chrome";

/**
 * Model-scoped slot for addon-contributed form sections. The addon that owns a
 * model's `ImplClassField` impls contributes the section that impl enables (e.g.
 * the OIDC login tab the iam addon adds to integrate's OAuth client form), gating
 * its fields with `showWhen` keyed on the impl value — so the base form stays
 * agnostic to the contributing addon's fields and a project without that addon
 * composes cleanly. Contributed `<Group>`/`<Action>` content parses like declared
 * children, so the fields join the form's values, selection, and submit.
 */
export const FORM_VIEW_SECTIONS_SLOT = "form-view.sections";

/** The model-scoped section-slot name an addon contributes form groups/actions to. */
export function formViewSectionsSlot(resource: string): string {
  return `${FORM_VIEW_SECTIONS_SLOT}:${resource}`;
}

type Values = Record<string, unknown>;
type RowRecord = BaseRecord & Row;
/** The child-lines the form is seeded/reset with, threaded into a values object. */
interface LinesSeed {
  field: string;
  rows: readonly Row[];
}

const TITLE_TEXT_CLASS =
  "block w-full min-w-0 truncate text-28 font-semibold leading-9 text-fg";
const TITLE_INPUT_CLASS =
  "h-auto min-h-9 rounded-none border-0 bg-transparent px-0 py-0 shadow-none " +
  "text-28 font-semibold leading-9 hover:border-transparent focus:border-transparent " +
  "focus:bg-transparent focus-visible:border-transparent placeholder:text-fg-subtle";
// Interim: wrapper styling strips current widget chrome; widget recipes should own an appearance variant later.
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
const FULL_FIELD_CLASS = "col-span-full";
// The form's centered content column — shared by the form body and the
// `recordExtras`/`recordTabs` panels around it so the column width lives in one place.
const FORM_COLUMN_CLASS = "mx-auto w-full max-w-[1100px] px-6 sm:px-8";

export function FormView({
  resource,
  id,
  fields,
  groups,
  children,
  actions,
  returning,
  defaultValues,
  onSaved,
  submit,
  submitLabel,
  toolbarStart,
  toolbar,
  recordExtras,
  recordTabs,
  deleteAction,
  deleteVisibleWhen,
  layout = "stacked",
  className,
}: FormViewProps): React.ReactElement {
  const t = useUiT();
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
  const childFields = React.useMemo(
    () => parsePageFields(children),
    [children],
  );
  const childGroups = React.useMemo(
    () => parsePageGroups(children),
    [children],
  );
  const childActions = React.useMemo(
    () => parsePageActions(children),
    [children],
  );
  const modelMetadata = useModelMetadata(resource);
  const schemaMetadata = useSchemaFieldMetadata();
  const formOverride = useFormOverride(resource);
  // Host/addon-contributed record chrome (star/share/…); base ships none.
  const recordChrome = useSlot(FORM_VIEW_RECORD_CHROME_SLOT);
  // Addon-contributed form sections for this model (e.g. the OIDC login tab the
  // iam addon adds to the OAuth client form). Parsed like declared children so
  // their fields join the form's values/selection/submit; each contribution gates
  // its own fields with `showWhen` keyed on the impl value.
  const sectionEntries = useSlot(formViewSectionsSlot(resource));
  const slotGroups = React.useMemo(
    () =>
      sectionEntries.flatMap((entry) =>
        parsePageGroups(entry.content as React.ReactNode),
      ),
    [sectionEntries],
  );
  const slotActions = React.useMemo(
    () =>
      sectionEntries.flatMap((entry) =>
        parsePageActions(entry.content as React.ReactNode),
      ),
    [sectionEntries],
  );
  const isCreate = id == null;
  // An addon may register a declarative create form for a model (composed into the
  // runtime). On create it replaces the declared/metadata fields, so ResourceList "New"
  // and the relation-picker inline create render the same form for that model. A
  // malformed registration (not an element) reads as no override, not a blank form.
  const overrideNode =
    isCreate && React.isValidElement(formOverride) ? formOverride : null;
  const overrideFields = React.useMemo(
    () => (overrideNode != null ? parsePageFields(overrideNode) : null),
    [overrideNode],
  );
  const overrideGroups = React.useMemo(
    () => (overrideNode != null ? parsePageGroups(overrideNode) : null),
    [overrideNode],
  );
  const overrideActions = React.useMemo(
    () => (overrideNode != null ? parsePageActions(overrideNode) : null),
    [overrideNode],
  );
  const declaredFields = React.useMemo(
    () => overrideFields ?? fields ?? childFields,
    [childFields, fields, overrideFields],
  );
  const declaredGroups = React.useMemo(
    () => [
      ...(overrideGroups ?? groups ?? childGroups),
      ...slotGroups,
    ],
    [childGroups, groups, overrideGroups, slotGroups],
  );
  const declaredActions = React.useMemo(
    () => [
      ...(overrideActions ?? actions ?? childActions),
      ...slotActions,
    ],
    [actions, childActions, overrideActions, slotActions],
  );
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
  // A `widget="slug"` field with no explicit `slugFrom` derives from the record's
  // title field.
  const defaultSlugSource = React.useMemo(
    () => formFields.find((field) => field.title)?.name,
    [formFields],
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
      // read type; selecting it makes the whole detail/return query invalid
      // and the record loads as null. Resource metadata owns which fields are
      // readable — skip a declared field once metadata is loaded and lacks it.
      if (modelMetadata && !modelMetadata.fields[field.name]) continue;
      addFieldSelection(paths, field, relationByField.get(field.name));
    }
    // Editable child lines (F6) are rendered by EditableLines, not as form
    // fields, so the loop above never selects them. Fold the lines relation and
    // its child columns into the detail read — the metadata projection owns which
    // columns, mirroring the `<resource>_save` return — so an existing record's
    // lines seed the composer instead of reading as absent. On create there is no
    // record and the diff-apply is pk-keyed, so the lines are skipped.
    const lines = isCreate ? null : modelMetadata?.resource?.linesResource;
    if (lines?.field) {
      for (const path of lineReadSelectionPaths(lines, schemaMetadata)) {
        paths.add(`${lines.field}.${path}`);
      }
    }
    for (const extra of returning ?? []) paths.add(extra);
    return [...paths];
  }, [formFields, isCreate, modelMetadata, relationByField, returning, schemaMetadata]);

  const dataResource = modelMetadata?.resource ?? null;
  const refineFields = React.useMemo(
    () => refineFieldsFromPaths(selection),
    [selection],
  );
  const refineResource = dataResource ? refineResourceName(dataResource) : "";
  const emptyValues = React.useMemo(
    () => emptyDraft(formFields, defaultValues),
    [defaultValues, formFields],
  );
  const [patchedRecord, setPatchedRecord] = React.useState<Row | null>(null);
  // `useForm` re-seeds an untouched form whenever `defaultValues` deep-changes.
  // Source it from this stable baseline ref (reassigned only on record seed,
  // post-save reset, and create reset) so a post-save re-render carrying new
  // field-descriptor identities can't re-seed and blank the just-saved values.
  const baselineValuesRef = React.useRef<Values>(emptyValues);
  // Slug fields that auto-derive until the user edits them; a manual edit lands the
  // field's name here and stops the derive for this form instance.
  const manualSlugFieldsRef = React.useRef<Set<string>>(new Set());
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
  const requiredFieldNames = React.useMemo<ReadonlySet<string>>(() => {
    const required = new Set(modelMetadata?.rootFields?.requiredCreateFields ?? []);
    return new Set(
      formFields
        .filter((field) => required.has(field.name) && !field.readOnly)
        .map((field) => field.name),
    );
  }, [formFields, modelMetadata]);
  const writableFieldNames = React.useMemo<ReadonlySet<string> | null>(() => {
    const fields = isCreate
      ? modelMetadata?.rootFields?.createFields
      : modelMetadata?.rootFields?.updateFields;
    return fields ? new Set(fields) : null;
  }, [isCreate, modelMetadata]);
  const form = useRefineForm<RowRecord, HttpError, Values>({
    defaultValues: baselineValuesRef.current,
    disableServerSideValidation: true,
    refineCoreProps: {
      action: isCreate ? "create" : "edit",
      resource: refineResource || "__angee_disabled__",
      id: isCreate ? undefined : (id as BaseKey | undefined),
      dataProviderName: dataResource?.schemaName,
      meta: { fields: refineFields },
      redirect: false,
      invalidates: isCreate ? ["list", "many"] : ["list", "many", "detail"],
      queryOptions: {
        enabled:
          !isCreate &&
          id !== null &&
          id !== undefined &&
          id !== "" &&
          dataResource !== null &&
          Boolean(dataResource.roots.detail),
      },
    },
  });
  const record = (form.refineCore.query?.data?.data as Row | undefined) ?? null;
  const displayRecord = patchedRecord ?? record;
  const loading = form.refineCore.query?.isFetching ?? false;
  const refetch = React.useCallback(() => {
    void form.refineCore.query?.refetch();
  }, [form.refineCore.query]);
  // The record a chrome contribution renders against — provided around the
  // record-chrome slot outlet so a contribution self-gates on the resource and
  // reads the record id without probing the URL. Only a saved record has one.
  const recordChromeContext = React.useMemo<RecordChromeContext | null>(
    () =>
      isCreate || id == null
        ? null
        : {
            resource,
            recordId: rowPublicId(displayRecord) ?? id,
            record: displayRecord ?? null,
          },
    [displayRecord, id, isCreate, resource],
  );

  // Editable document lines (F6): active only when the resource metadata carries a
  // `linesResource`, on an existing record — the `<resource>_save` diff-apply is
  // pk-keyed, so lines are edited on a saved document, not staged on create. A
  // custom `submit` owner (a resource with no stock save root) receives the diff
  // instead.
  const linesResource = dataResource?.linesResource ?? null;
  const linesConfig = React.useMemo(
    () => (linesResource ? lineDiffConfig(linesResource) : null),
    [linesResource],
  );
  const linesField = linesResource?.field ?? null;
  const saveOperation = useSaveOperation(dataResource);
  const resourceSave = useAngeeResourceSave(saveOperation.target, {
    document: saveOperation.document,
  });
  const invalidate = useInvalidate();
  const linesActive =
    !isCreate &&
    linesConfig !== null &&
    linesField !== null &&
    (saveOperation.target !== null || Boolean(submit));
  // The loaded record's lines, stabilized per record reference: fresh row objects
  // read as unequal in the reseed comparison and would loop, so the seed threads
  // this one array. Also the diff baseline the submit path measures edits against.
  const seedLineRows = React.useMemo(
    () =>
      linesActive && linesConfig && linesField
        ? recordLinesToRows(record?.[linesField], linesConfig)
        : null,
    [linesActive, linesConfig, linesField, record],
  );
  const linesSeed = React.useCallback(
    (rows: readonly Row[] | null | undefined): LinesSeed | undefined =>
      linesActive && linesField && rows ? { field: linesField, rows } : undefined,
    [linesActive, linesField],
  );
  const rowsFromRecord = React.useCallback(
    (source: Row | null | undefined): readonly Row[] | null =>
      linesActive && linesConfig && linesField
        ? recordLinesToRows(source?.[linesField], linesConfig)
        : null,
    [linesActive, linesConfig, linesField],
  );
  const invalidateResource = React.useCallback(async () => {
    if (!dataResource) return;
    await invalidate({
      resource: refineResourceName(dataResource),
      dataProviderName: dataResource.schemaName,
      id: id ?? undefined,
      invalidates: ["list", "many", "detail"],
    });
  }, [dataResource, id, invalidate]);

  // An existing-record form starts before its query resolves. Keep fields locked
  // until the record exists so a fast edit cannot be overwritten by the seed reset.
  const recordUnavailable = !isCreate && record == null;
  const formReadOnly = React.useMemo(
    () =>
      recordUnavailable ||
      (!submit &&
        !Boolean(isCreate ? dataResource?.roots.create : dataResource?.roots.update)) ||
      (formFields.length > 0 && formFields.every((field) => field.readOnly)),
    [dataResource, formFields, isCreate, recordUnavailable, submit],
  );
  const formIsDirty = form.formState.isDirty;
  const formIsDirtyRef = React.useRef(formIsDirty);
  React.useEffect(() => {
    formIsDirtyRef.current = formIsDirty;
  }, [formIsDirty]);
  useUnsavedChangesNavigationGuard({
    isDirty: formIsDirty,
    isDirtyNow: () => formIsDirtyRef.current,
    readOnly: formReadOnly,
  });

  const resetForm = React.useCallback(
    (values: Values, options: { keepDirtyValues?: boolean } = {}) => {
      form.reset(
        values,
        options.keepDirtyValues ? { keepDirtyValues: true } : undefined,
      );
      formIsDirtyRef.current = Boolean(options.keepDirtyValues && formIsDirtyRef.current);
    },
    [form],
  );
  const runSubmit = React.useCallback(
    async (data: Values, lines: LineDiff | null = null): Promise<Row | null> => {
      if (submit) {
        return (
          (await submit(data, {
            resource,
            id: id ?? null,
            isCreate,
            record: displayRecord ?? null,
            lines,
          })) ?? null
        );
      }
      // Dirty lines route through the transactional `<resource>_save(pk, patch,
      // lines)` diff-apply; a parent-only edit keeps the stock update path. The
      // custom mutation carries no resource invalidation, so refresh the list/detail
      // caches the plain update path would have invalidated.
      if (lines && lines.hasChanges && id != null && saveOperation.target !== null) {
        const saved = await resourceSave.save({
          pk: id,
          patch: data,
          lines: lines.payload,
        });
        if (saved) await invalidateResource();
        return saved;
      }
      const response = await form.refineCore.onFinish(data);
      return (response?.data ?? null) as Row | null;
    },
    [
      displayRecord,
      form.refineCore,
      id,
      invalidateResource,
      isCreate,
      resource,
      resourceSave,
      saveOperation.target,
      submit,
    ],
  );
  const commitSavedRecord = React.useCallback(
    (saved: Row, options: { notify: boolean }): void => {
      const savedValues = recordToValues(
        saved,
        formFields,
        linesSeed(rowsFromRecord(saved)),
      );
      baselineValuesRef.current = savedValues;
      setPatchedRecord(saved);
      resetForm(savedValues);
      // A reused, still-mounted create form starts each new record with a clean
      // slug-derive state (no `id` change fires the create-reset effect here).
      if (isCreate) manualSlugFieldsRef.current = new Set();
      if (options.notify) onSaved?.(saved);
    },
    [formFields, isCreate, linesSeed, onSaved, resetForm, rowsFromRecord],
  );
  const submitValues = React.useCallback(
    async (value: Values) => {
      setSaveError(null);
      setServerFieldErrors({});
      if (formReadOnly) {
        throw new Error(`Resource mutation for "${resource}" is disabled.`);
      }
      if (!dataResource) {
        throw new Error(`Resource metadata for "${resource}" is not available.`);
      }
      const data = mutationData(value, formFields, {
        dirtyFields: form.formState.dirtyFields as Record<string, unknown>,
        isCreate,
        writableFields: writableFieldNames,
      });
      // Diff the edited line rows against the baseline the form was last seeded
      // with (its stored server truth) — its output routes the submit through save.
      const linesDiff =
        linesActive && linesConfig && linesField
          ? diffLines(
              baselineLineRows(baselineValuesRef.current, linesField, seedLineRows),
              (value[linesField] as Row[] | undefined) ?? [],
              linesConfig,
            )
          : null;
      try {
        const saved = await runSubmit(data, linesDiff);
        if (saved) {
          commitSavedRecord(saved, { notify: true });
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
              ? fieldValidationSummary(fieldErrors, fieldByName, t)
              : t("form.genericSaveError"),
        );
      }
    },
    [
      dataResource,
      fieldByName,
      formFields,
      formReadOnly,
      isCreate,
      commitSavedRecord,
      linesActive,
      linesConfig,
      linesField,
      resource,
      runSubmit,
      seedLineRows,
      writableFieldNames,
      form.formState.dirtyFields,
    ],
  );
  const submitForm = form.handleSubmit(submitValues);

  // Apply a record action's field patch through the form's own update mutation
  // and re-seed from the result — the same path as a save, so the form shows the
  // new values without a refetch. Reused by `set` actions and the action context.
  const applyPatch = React.useCallback(
    async (patch: Record<string, unknown>): Promise<Row | null> => {
      if (id == null) {
        throw new Error("No open record to update.");
      }
      if (formReadOnly) {
        throw new Error(`Resource mutation for "${resource}" is disabled.`);
      }
      const saved = await runSubmit(patch);
      if (saved) {
        commitSavedRecord(saved, { notify: false });
        setSaveError(null);
        setServerFieldErrors({});
      }
      return saved;
    },
    [commitSavedRecord, formReadOnly, id, resource, runSubmit],
  );

  const patchRecord = React.useCallback(
    (patch: Record<string, unknown>): void => {
      const source = patchedRecord ?? record;
      if (!source) return;
      const next = { ...source, ...patch };
      const nextValues = recordToValues(
        next,
        formFields,
        linesSeed(rowsFromRecord(next)),
      );
      setPatchedRecord(next);
      baselineValuesRef.current = nextValues;
      resetForm(nextValues);
      setSaveError(null);
      setServerFieldErrors({});
    },
    [formFields, linesSeed, patchedRecord, record, resetForm, rowsFromRecord],
  );

  React.useEffect(() => {
    setPatchedRecord(null);
  }, [record]);

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
  }, [resource, id]);

  React.useEffect(() => {
    if (isCreate) {
      if (seededIdRef.current !== null) {
        seededIdRef.current = null;
        seededRecordRef.current = null;
        baselineValuesRef.current = emptyValues;
        manualSlugFieldsRef.current = new Set();
        resetForm(emptyValues);
        setSaveError(null);
      }
      return;
    }
    const recordId = rowPublicId(record);
    // Re-seed on a new record id, or when the record *object reference* changes
    // for the same id — a refetch landed with fresh server state (e.g. after a
    // record action). Keying off the reference (not a manual flag) means a stale
    // intervening render carrying the same record can't consume the re-seed.
    if (record && recordId) {
      const recordValues = recordToValues(
        record,
        formFields,
        linesSeed(seedLineRows),
      );
      const recordChanged =
        seededIdRef.current !== recordId || seededRecordRef.current !== record;
      const cleanFieldShapeChanged =
        !recordChanged &&
        patchedRecord === null &&
        !formIsDirtyRef.current &&
        !formValuesEqual(recordValues, baselineValuesRef.current);
      if (!recordChanged && !cleanFieldShapeChanged) return;
      seededIdRef.current = recordId;
      seededRecordRef.current = record;
      const keepDirtyValues =
        seededIdRef.current === recordId && formIsDirtyRef.current;
      baselineValuesRef.current = recordValues;
      // Same-record refetches can land while the user is editing; keep their
      // dirty values while updating RHF's clean baseline to the fresh server row.
      resetForm(recordValues, { keepDirtyValues });
      setSaveError(null);
    }
  }, [
    emptyValues,
    formFields,
    isCreate,
    linesSeed,
    patchedRecord,
    record,
    resetForm,
    seedLineRows,
  ]);

  const titleField = titleFieldFor(formFields, modelMetadata);
  const titleFieldMessages = titleField
    ? [
        ...fieldErrorMessages(
          form.formState.errors[titleField.name]
            ? [form.formState.errors[titleField.name]]
            : [],
        ),
        ...(serverFieldErrors[titleField.name] ?? []),
      ]
    : [];
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
    () =>
      recordSubtitleParts(
        displayRecord,
        id,
        recordSubtitleFields(modelMetadata),
        t,
      ),
    [displayRecord, id, modelMetadata, t],
  );
  const requiredMessage = t("form.required");
  // Project the flat server field errors keyed `<linesField>.<index>.<childField>`
  // into the per-row error contract the lines composer renders under each cell.
  const lineRowErrors = React.useMemo(
    () =>
      linesActive && linesField
        ? projectLineRowErrors(serverFieldErrors, linesField)
        : undefined,
    [linesActive, linesField, serverFieldErrors],
  );
  const renderField = (field: FieldDescriptor): React.ReactNode => {
    const relation = relationByField.get(field.name);
    // The selected record's label comes from the parent read (folded above), so
    // the picker shows it with no extra query — derived during render, never
    // stored in form state (which stays the flat id).
    const selectedOption = relation
      ? relationSelectedOption(displayRecord?.[field.name], relation.labelField)
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

  // Apply a field's `prefill` seeds (the impl-defaults mechanism): picking an impl
  // loads its full preset, so every declared default lands — including booleans the
  // model leaves `false` (e.g. `login_enabled`), which a blank-only merge would skip.
  // Create-only impl fields seed only while new; editable impl fields may deliberately
  // reseed sibling fields on saved rows (e.g. switching an inference backend).
  function applyFieldPrefill(field: FieldDescriptor, value: unknown): void {
    if (!isCreate && field.createOnly) return;
    const seeds = field.prefill?.(value);
    if (!seeds) return;
    for (const [name, seed] of Object.entries(seeds)) {
      form.setValue(name, seed, { shouldDirty: true, shouldTouch: true });
    }
  }

  // Auto-derive `widget="slug"` fields from their source (the record title by
  // default) while creating. A direct edit to a slug field switches it to manual
  // and stops the derive; the programmatic `setFieldValue` here never marks manual.
  function applySlugDerivation(field: FieldDescriptor, value: unknown): void {
    if (fieldWidgetId(field) === "slug") {
      manualSlugFieldsRef.current.add(field.name);
      return;
    }
    if (!isCreate) return;
    for (const slugField of formFields) {
      if (fieldWidgetId(slugField) !== "slug") continue;
      if (manualSlugFieldsRef.current.has(slugField.name)) continue;
      if ((slugField.slugFrom ?? defaultSlugSource) !== field.name) continue;
      form.setValue(slugField.name, slugify(value), {
        shouldDirty: true,
        shouldTouch: true,
      });
    }
  }

  // The onChange pipeline shared by every editable field — grid (`renderField`) and
  // the header title/body/status fields. Routing them all here keeps onChange-driven
  // behavior (impl prefill, slug derivation) from silently skipping the header fields
  // — which is how the title-source slug derive was lost.
  function afterFieldChange(field: FieldDescriptor, value: unknown): void {
    applyFieldPrefill(field, value);
    applySlugDerivation(field, value);
  }

  function fieldReadOnly(field: FieldDescriptor): boolean {
    return recordUnavailable || Boolean(field.readOnly);
  }

  const recordPanelContext: RecordPanelContext | null =
    !isCreate && id != null ? { recordId: id, reload } : null;
  const toolbarStartNode =
    typeof toolbarStart === "function"
      ? toolbarStart({
          recordId: id ?? null,
          record: displayRecord ?? null,
          patchRecord,
          reload,
        })
      : toolbarStart;
  const visibleDeleteAction =
    deleteAction === undefined ||
    (deleteVisibleWhen !== undefined &&
      (displayRecord == null || !deleteVisibleWhen(displayRecord)))
      ? undefined
      : deleteAction;
  const recordTabList = recordTabs ?? [];
  const tabbed = recordPanelContext != null && recordTabList.length > 0;

  // Render a list of section models in the chosen layout. `"stacked"` keeps each
  // labelled group as its own section; `"tabs"` renders the labelled groups as tab
  // panels (and stacks any unlabelled/ungrouped section above the strip). The same
  // visibility-filtered list flows through both, so `showWhen` is honored either way.
  const renderSectionModels = (
    list: readonly FormSectionModel[],
  ): React.ReactNode => {
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

  // The form body: field sections then the prominent body field. Shown inline on a
  // plain form, or wrapped as the leading "Overview" tab when `recordTabs` is set.
  const overviewBody = (
    <>
      <div className="grid gap-6">
        {hasConditionalFields ? (
          <ConditionalSections
            control={form.control}
            sections={sections}
            renderSections={renderSectionModels}
          />
        ) : (
          renderSectionModels(sections)
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
            rules={fieldValidationRules(bodyField, requiredFieldNames, requiredMessage)}
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
        void submitForm(event);
      }}
    >
      <ControlBand className={formIsDirty ? "bg-brand-soft" : undefined}>
        <div className="flex min-w-0 items-center gap-2">
          {toolbarStartNode}
          {isCreate || formIsDirty ? (
            <div className="flex items-center gap-2">
              {formIsDirty ? (
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  disabled={form.refineCore.mutation.isPending || form.formState.isSubmitting}
                  onClick={() => resetForm(baselineValuesRef.current)}
                >
                  {t("form.discard")}
                </Button>
              ) : null}
              <Button
                type="button"
                variant="primary"
                size="sm"
                loading={form.refineCore.mutation.isPending || form.formState.isSubmitting}
                disabled={formReadOnly}
                onClick={() => {
                  void submitForm();
                }}
              >
                {submitLabel ?? (isCreate ? t("form.create") : t("form.save"))}
              </Button>
            </div>
          ) : null}
          {declaredActions.length > 0 || visibleDeleteAction !== undefined ? (
            <RecordActionBar
              record={displayRecord ?? null}
              actions={declaredActions}
              applyPatch={applyPatch}
              reload={reload}
              deleteAction={visibleDeleteAction}
            />
          ) : null}
        </div>
        <div className="min-w-2 flex-1" />
        <div className="flex min-w-0 items-center gap-2">
          {recordChromeContext ? (
            <RecordChromeProvider value={recordChromeContext}>
              <SlotOutlet entries={recordChrome} />
            </RecordChromeProvider>
          ) : null}
          {toolbar}
        </div>
      </ControlBand>
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
                <Controller
                  control={form.control}
                  name={titleField.name}
                  rules={fieldValidationRules(titleField, requiredFieldNames, requiredMessage)}
                  render={({ field: controller }) => (
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
                  )}
                />
              ) : (
                <h1 className="truncate text-28 font-semibold leading-9 text-fg">
                  {t("form.record")}
                </h1>
              )}
              {/* The title field renders in the header (no FieldRoot), so its
                  server validation error is surfaced here rather than inline. */}
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
                  rules={fieldValidationRules(statusField, requiredFieldNames, requiredMessage)}
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
          keepMounted={tab.keepMounted}
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
  isDirtyNow,
  readOnly,
}: {
  isDirty: boolean;
  isDirtyNow: () => boolean;
  readOnly: boolean;
}): void {
  const confirm = useConfirm();
  const t = useUiT();
  const shouldBlockFn = React.useCallback(async () => {
    // Read the live store, not the captured render value. A successful save resets
    // the form (isDirty → false) and navigates in the same tick, before the React
    // re-render flushes — a stale `true` here would wrongly block the post-save
    // redirect with a phantom "unsaved changes" prompt.
    if (readOnly || !isDirtyNow()) return false;
    const leave = await confirm({
      title: t("form.unsavedLeaveTitle"),
      cancel: t("form.stay"),
      confirm: t("form.leave"),
      danger: true,
    });
    return !leave;
  }, [confirm, isDirtyNow, readOnly, t]);

  useBlocker({
    shouldBlockFn,
    enableBeforeUnload: isDirty && !readOnly,
    disabled: readOnly || !isDirty,
  });
}

type FormSectionModel = {
  key: string;
  label?: React.ReactNode;
  columns?: number;
  fields: readonly FieldDescriptor[];
};

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
    <div className={cn(textRoleVariants({ role: "meta" }), "mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 font-mono")}>
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

/**
 * Render labelled groups as tabs (one panel per group) instead of stacked
 * sections. The group label becomes the tab; the panel drops the in-body eyebrow
 * (the tab already names it). Active tab is derived/guarded so a group that drops
 * out (all its fields hidden by `showWhen`) falls back to the first remaining tab
 * rather than leaving an empty panel.
 */
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

/**
 * Subscribe to live form values only when `showWhen` fields exist, so the watch
 * stays isolated in this child: a keystroke re-renders this subtree to re-filter
 * `visibleSections`, not the whole FormView (title/body/markdown editor, header,
 * toolbar). `useWatch({ control })` with no `name` returns the full values object.
 */
function ConditionalSections({
  control,
  sections,
  renderSections,
}: {
  control: Control<Values>;
  sections: readonly FormSectionModel[];
  renderSections: (list: readonly FormSectionModel[]) => React.ReactNode;
}): React.ReactNode {
  const values = useWatch({ control }) as Values;
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
          effectiveReadOnly ? READONLY_FIELD_CONTROL_CLASS : EDITABLE_FIELD_CONTROL_CLASS,
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

function titleText(value: unknown, fallback: string): string {
  const text = String(value ?? "").trim();
  return text || fallback;
}

function addFieldSelection(
  paths: Set<string>,
  field: FieldDescriptor,
  relation?: RelationFieldInfo,
): void {
  if (isRelationIdField(field)) {
    paths.add(`${field.name}.id`);
    // Fold the related record's label into the parent read so a read-only/show
    // view labels the relation with no extra round-trip. `labelField` is the
    // related type's `recordRepresentation`, always a readable scalar field, so
    // the nested selection is safe.
    if (relation && relation.labelField !== "id") {
      paths.add(`${field.name}.${relation.labelField}`);
    }
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
    // Seed precedence (a live user edit later overrides all of these): a
    // page-level `defaultValues` entry (the list-scope create seed a
    // `ResourceList` passes to match its active filter) wins, then the field's
    // own `defaultValue`, then the widget's empty value.
    draft[field.name] = Object.hasOwn(defaultValues ?? {}, field.name)
      ? defaultValues?.[field.name]
      : field.defaultValue !== undefined
        ? field.defaultValue
        : emptyValue(field);
  }
  return draft;
}

function recordToValues(
  record: Row,
  fields: readonly FieldDescriptor[],
  lines?: LinesSeed,
): Values {
  const values: Values = {};
  for (const field of fields) {
    // A `many2one` relation seeds with the record's raw relation value — its nested
    // `{ id, <label> }` object, or `null` when unset — the exact shape refine's
    // `useForm` re-applies to the form values (`applyValuesToFields`) after this
    // seed. Normalizing it to a bare id here instead would leave the clean baseline
    // disagreeing with what refine writes into the live values, so `formState.isDirty`
    // reads true on an untouched record (a spurious unsaved-changes guard). The picker
    // and submit read the id back out via `relationValueId`; only a user pick replaces
    // the object with a bare id, which is then the sole dirty field.
    values[field.name] = isRelationIdField(field)
      ? record[field.name] ?? null
      : recordFieldValue(record, field) ?? emptyValue(field);
  }
  // The child-lines array is not a declared FieldDescriptor, so it is seeded here
  // (a stable reference from the caller) — a plain `form.reset(values)` would
  // otherwise blank the `useFieldArray` the lines composer binds.
  if (lines) values[lines.field] = lines.rows;
  return values;
}

/**
 * The line rows the form was last seeded with — the baseline a submit diffs the
 * edited rows against. The baseline values ref carries them (record seed, saved
 * reset, or record-action patch); before the first seed it falls back to the
 * record-derived rows so an unedited save is a no-op.
 */
function baselineLineRows(
  baseline: Values,
  field: string,
  fallback: readonly Row[] | null,
): readonly Row[] {
  const rows = baseline[field];
  return Array.isArray(rows) ? (rows as readonly Row[]) : fallback ?? [];
}

/**
 * Project the flat server field errors — keyed `<linesField>.<index>.<childField>`
 * — into the per-row `ValidationErrors` array the lines composer renders under each
 * cell. A key that names no row index is left to the form-level error surfaces.
 */
function projectLineRowErrors(
  errors: Record<string, readonly string[]>,
  field: string,
): readonly (ValidationErrors | undefined)[] | undefined {
  const prefix = `${field}.`;
  const byRow = new Map<number, ValidationErrors>();
  for (const [key, messages] of Object.entries(errors)) {
    if (!key.startsWith(prefix)) continue;
    const match = /^(\d+)\.(.+)$/.exec(key.slice(prefix.length));
    if (!match) continue;
    const index = Number(match[1]);
    const childField = match[2] as string;
    const entry = byRow.get(index) ?? { fieldErrors: {}, formErrors: [] };
    entry.fieldErrors[childField] = [
      ...(entry.fieldErrors[childField] ?? []),
      ...messages,
    ];
    byRow.set(index, entry);
  }
  if (byRow.size === 0) return undefined;
  const rows: (ValidationErrors | undefined)[] = [];
  const maxIndex = Math.max(...byRow.keys());
  for (let index = 0; index <= maxIndex; index += 1) {
    rows.push(byRow.get(index));
  }
  return rows;
}

function recordFieldValue(record: Row, field: FieldDescriptor): unknown {
  const value = record[field.name];
  const optionValue = canonicalOptionValue(field.options, value);
  if (optionValue !== undefined) return optionValue;
  if (!isRelationIdField(field)) return value;
  if (typeof value === "string") return value;
  if (isRecord(value)) return rowPublicId(value) ?? value;
  return value;
}

/** Whether a field is shown for the current values — false hides and never submits it. */
function isFieldVisible(field: FieldDescriptor, values: Values): boolean {
  return !field.showWhen || field.showWhen(values);
}

function fieldValidationRules(
  field: FieldDescriptor,
  requiredFieldNames: ReadonlySet<string>,
  requiredMessage: string,
): { validate: (value: unknown) => true | string } | undefined {
  if (field.readOnly || !requiredFieldNames.has(field.name)) return undefined;
  return {
    validate: (value) => !isEmptyFieldValue(value) || requiredMessage,
  };
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
    dirtyFields: Record<string, unknown>;
    id?: string | null;
    isCreate: boolean;
    writableFields?: ReadonlySet<string> | null;
  },
): Values {
  const data: Values = {};
  for (const field of fields) {
    if (options.writableFields && !options.writableFields.has(field.name)) {
      continue;
    }
    // A read-only field is normally not submitted. Exception: on create, a field
    // carrying a `defaultValue` submits its create-seeded default even when it is
    // `readOnly`/`createOnly` — so a fixed, non-editable field still sends its
    // value. `editOnly` fields stay excluded on create (mode-locked read-only,
    // and the create input omits them).
    const seededDefault =
      options.isCreate && field.defaultValue !== undefined && !field.editOnly;
    if (field.readOnly && !seededDefault) continue;
    // A field hidden by its `showWhen` predicate is not part of the record.
    if (!isFieldVisible(field, values)) continue;
    const next = mutationFieldValue(field, values[field.name]);
    if (isUnselectedOption(field, next)) continue;
    // Blank numeric create fields should let the GraphQL input default apply.
    // Sending "" fails Int/Float coercion, and sending null fails non-null fields
    // with defaults such as `Int! = 0`.
    if (options.isCreate && isEmptyNumericValue(field, next)) continue;
    if (!options.isCreate && !options.dirtyFields[field.name]) {
      continue;
    }
    data[field.name] = next;
  }
  if (!options.isCreate && options.id != null) data.id = options.id;
  return data;
}

function mutationFieldValue(field: FieldDescriptor, value: unknown): unknown {
  if (isRelationIdField(field)) return relationValueId(value);
  return value;
}

function emptyValue(field: FieldDescriptor): unknown {
  if (isNumericField(field)) return null;
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

function isNumericField(field: FieldDescriptor): boolean {
  const id = fieldWidgetId(field);
  return id === "integer" || id === "float";
}

function isEmptyNumericValue(field: FieldDescriptor, value: unknown): boolean {
  return isNumericField(field) && value == null;
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

function formValuesEqual(left: Values, right: Values): boolean {
  const keys = new Set([...Object.keys(left), ...Object.keys(right)]);
  for (const key of keys) {
    if (!valuesEqual(left[key], right[key])) return false;
  }
  return true;
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

function fieldValidationSummary(
  fieldErrors: Record<string, readonly string[]>,
  fieldByName: ReadonlyMap<string, FormField>,
  t: UiTranslate,
): string {
  const fields = Object.keys(fieldErrors).map(
    (name) => fieldByName.get(name)?.label || name,
  );
  return fields.length > 0
    ? t("form.fixHighlightedFieldsNamed", { fields: fields.join(", ") })
    : t("form.fixHighlightedFields");
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
  fields: RecordSubtitleFields,
  t: UiTranslate,
): React.ReactNode[] {
  const parts: React.ReactNode[] = [];
  const recordId = presentValue(record?.id) ?? presentValue(id);
  if (recordId !== undefined) parts.push(recordIdLabel(String(recordId)));
  if (record) {
    const created = fieldValue(record, fields.created);
    const updated = fieldValue(record, fields.updated);
    const words = fieldValue(record, fields.wordCount);
    if (created !== undefined) {
      parts.push(t("form.created", { value: formatRecordDate(created) }));
    }
    if (updated !== undefined) {
      parts.push(t("form.updated", { value: formatRecordDate(updated) }));
    }
    if (words !== undefined) parts.push(formatWordCount(words, t));
  }
  return parts.filter((part) => String(part).trim() !== "");
}

function fieldValue(record: Row, field: string | undefined): unknown | undefined {
  return field ? presentValue(record[field]) : undefined;
}

function presentValue(value: unknown): unknown | undefined {
  if (value == null) return undefined;
  if (typeof value === "string" && value.trim() === "") return undefined;
  return value;
}

/** Prefer the human-facing public id in the subtitle. */
function recordIdLabel(value: string): string {
  return publicIdLabel(value) ?? shortRecordId(value);
}

function shortRecordId(value: string): string {
  const text = value.trim();
  if (text.length <= 12) return text;
  return text.slice(0, 8);
}

function formatRecordDate(value: unknown): string {
  const date = dateFromUnknown(value);
  return date ? formatDate(date) : String(value);
}

function formatWordCount(
  value: unknown,
  t: UiTranslate,
): string {
  const count =
    typeof value === "number"
      ? value
      : typeof value === "string"
        ? Number(value)
        : Number.NaN;
  if (Number.isFinite(count)) {
    return t("form.wordCount", { count: new Intl.NumberFormat().format(count) });
  }
  return t("form.wordCount", { count: String(value) });
}

function normaliseFieldName(value: string): string {
  return value.replace(/[-_\s]+/g, "").toLowerCase();
}
