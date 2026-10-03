import * as React from "react";
import { developmentMode } from "../../lib/development-mode";
import {
  lineReadSelectionPaths,
  modelFieldForPath,
  refineResourceName,
  relationRepresentationForPath,
  useModelMetadata,
  useSchemaFieldMetadata,
  type ModelMetadata,
  type Row,
  holdsPermission,
} from "@angee/metadata";
import { refineFieldsFromPaths } from "@angee/refine";
import { useOne } from "@refinedev/core";

import { useContainer, useFormOverride, modelChain } from "../../runtime";
import { useUiT, type UiTranslate } from "../../i18n";
import {
  hasDirectPageElement,
  hasPageField,
  pageChildren,
  pageElementKind,
  parsePageActions,
  parsePageFields,
  parsePageGroups,
  parsePageTabs,
  type FieldDescriptor,
  type GroupDescriptor,
  type TabDescriptor,
  type TabLabel,
} from "../page";
import {
  fieldsWithMetadataDefaults,
  relationFieldInfoForDescriptor,
  type RelationFieldInfo,
} from "../resource/model-metadata-defaults";
import type { RecordActionDescriptor, RecordDeleteAction } from "./RecordActionBar";
import { recordRailGroups, visibleRecordRailGroups, type RecordRailGroupProps } from "./form-view-rail";
import { rowDependentChildren, useContainerAdmission } from "./container-admission";
import {
  addFieldSelection,
  fieldErrorMessages,
  flattenedFormFields,
  formSections,
  formViewFieldLayout,
  recordSubtitleParts,
  withModeLockedFields,
  type FormSectionModel,
} from "./form-view-model";
import {
  lineRowErrorsFromDottedPaths,
  type ValidationErrors,
} from "./validation-errors";
import {
  useFormViewRecordChrome,
  type FormViewRecordChromeSurface,
} from "./use-form-view-record-chrome";
import {
  useFormViewSave,
  type FormSubmit,
  type FormViewAcknowledgedSource,
  type FormViewSaveSurface,
} from "./use-form-view-save";

export type {
  FormSubmit,
  FormSubmitAcknowledgement,
  FormSubmitContext,
  FormViewAcknowledgedSource,
} from "./use-form-view-save";
export { acknowledgeFormSubmit } from "./use-form-view-save";

/** Value of the form body's leading tab, shown when record tabs are declared. */
export const FORM_VIEW_OVERVIEW_TAB_ID = "overview";

export type RecordPresentation = "document" | "workspace";

export interface RecordFieldFocusOptions {
  recordTabId?: string;
}

export interface OverviewTabOptions {
  label?: React.ReactNode;
  position?: "first" | "last";
  /** No Overview tab: the first record tab is the default. With no record tabs the overview still renders. */
  hidden?: boolean;
}

export interface RecordPanelContext {
  recordId: string;
  reload: () => void;
  form: FormViewSaveSurface;
  /** Switch tabs, then focus one registered RHF field after that tab mounts. */
  focusField: (path: string, options?: RecordFieldFocusOptions) => void;
}

export interface RecordToolbarContext {
  recordId: string | null;
  record: Row | null;
  patchRecord: (patch: Record<string, unknown>) => void;
  reload: () => void;
  form: FormViewSaveSurface;
}

export interface RecordTabDescriptor {
  id: string;
  label: TabLabel;
  /** Fill the available content area beneath compact record chrome. Other tabs retain document layout. */
  presentation?: "full-bleed";
  icon?: React.ReactNode;
  /** Rendered as a `Tabs.Count` beside the label (a count, a status dot). */
  badge?: React.ReactNode;
  /** Active lets a retained panel suspend queries and shell publications. */
  render: (context: RecordPanelContext & { active: boolean }) => React.ReactNode;
  /** Shown only while the loaded record satisfies it; absent until the record loads. */
  visibleWhen?: (record: Row) => boolean;
  /**
   * Keep the panel's React state and effects mounted while inactive. Base UI
   * mounts it eagerly; render receives active=false and must suspend its reads
   * and shell publications explicitly. Persistent effects such as unsaved-edit
   * navigation guards remain mounted. This differs from React Activity, which
   * tears down hidden effects, including those guards.
   */
  keepMounted?: boolean;
}

export interface UseFormViewSurfaceProps {
  resource: string;
  id?: string | null;
  /** Lock fields and generated CRUD; explicitly declared custom actions remain available. */
  readOnly?: boolean;
  fields?: readonly FieldDescriptor[];
  groups?: readonly GroupDescriptor[];
  children?: React.ReactNode;
  actions?: readonly RecordActionDescriptor[];
  returning?: readonly string[];
  defaultValues?: Record<string, unknown>;
  acknowledgedSource?: FormViewAcknowledgedSource;
  onSaved?: (row: Row) => void;
  submit?: FormSubmit;
  createSubmit?: FormSubmit;
  /** Read-only policy evaluated on the saved record, never on the local draft. */
  readOnlyWhen?: (record: Row) => boolean;
  /** Called before a native field owner applies the first value in an interaction. */
  onFieldInteractionStart?: (path: string) => void;
  /** Called when that native field interaction is complete. */
  onFieldInteractionCommit?: (path: string) => void;
  /** Called after the native form baseline has been restored. */
  onDiscarded?: () => void;
  recordTabs?: readonly RecordTabDescriptor[];
  /** Selected saved-record tab when an outer owner, such as the router, controls it. */
  recordTab?: string;
  /** Called when the selected saved-record tab changes. */
  onRecordTabChange?: (tab: string) => void;
  defaultRecordTab?: string;
  /** Without an Overview tab an unavailable tab id falls back to the first record tab. */
  overviewHidden?: boolean;
  deleteAction?: RecordDeleteAction;
  deleteVisibleWhen?: (record: Row) => boolean;
}

export interface FormViewSurface
  extends FormViewSaveSurface,
    FormViewRecordChromeSurface {
  t: UiTranslate;
  activeRecordTab: string;
  requestedFocusPath: string | null;
  setActiveRecordTab: (tab: string) => void;
  isCreate: boolean;
  modelMetadata: ModelMetadata | null;
  formFields: readonly FieldDescriptor[];
  relationByField: ReadonlyMap<string, RelationFieldInfo>;
  hasConditionalFields: boolean;
  requiredMessage: string;
  titleField: FieldDescriptor | undefined;
  titlePlacementField: FieldDescriptor | undefined;
  titleFieldMessages: readonly string[];
  statusField: FieldDescriptor | undefined;
  bodyField: FieldDescriptor | undefined;
  sections: readonly FormSectionModel[];
  railGroups: readonly RecordRailGroupProps[];
  subtitleParts: readonly React.ReactNode[];
  lineRowErrors: readonly (ValidationErrors | undefined)[] | undefined;
  declaredActions: readonly RecordActionDescriptor[];
  actionsBlocked: boolean;
  recordPanelContext: RecordPanelContext | null;
  recordToolbarContext: RecordToolbarContext;
  recordTabList: readonly RecordTabDescriptor[];
  tabbed: boolean;
  visibleDeleteAction: RecordDeleteAction | undefined;
}

const EMPTY_RECORD_TABS: readonly RecordTabDescriptor[] = [];

/** Compose declarations, metadata, save state, and record chrome into one surface. */
export function useFormViewSurface({
  resource,
  id,
  readOnly = false,
  fields,
  groups,
  children,
  actions,
  returning,
  defaultValues,
  acknowledgedSource,
  onSaved,
  submit,
  createSubmit,
  readOnlyWhen,
  onFieldInteractionStart,
  onFieldInteractionCommit,
  onDiscarded,
  recordTabs,
  recordTab,
  onRecordTabChange,
  defaultRecordTab = FORM_VIEW_OVERVIEW_TAB_ID,
  overviewHidden = false,
  deleteAction,
  deleteVisibleWhen,
}: UseFormViewSurfaceProps): FormViewSurface {
  const t = useUiT();
  const [localRecordTab, setLocalRecordTab] = React.useState(defaultRecordTab);
  const requestedRecordTab = recordTab ?? localRecordTab;
  const setActiveRecordTab = React.useCallback((tab: string) => {
    if (recordTab === undefined) setLocalRecordTab(tab);
    onRecordTabChange?.(tab);
  }, [onRecordTabChange, recordTab]);
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
  const childFields = React.useMemo(() => parsePageFields(children), [children]);
  const childGroups = React.useMemo(() => parsePageGroups(children), [children]);
  const childActions = React.useMemo(() => parsePageActions(children), [children]);
  const modelMetadata = useModelMetadata(resource);
  const schemaMetadata = useSchemaFieldMetadata();
  const dataResource = modelMetadata?.resource ?? null;
  const modelLabel = dataResource?.modelLabel ?? "";
  const canonicalResource = dataResource?.canonicalLabel ?? modelLabel;
  const canonicalMetadata = useModelMetadata(canonicalResource);
  const formOverride = useFormOverride(modelLabel);
  // Children of `form#…` show on every form; a model's own, and its MTI parent's, on its records.
  const models = React.useMemo(
    () => modelChain(canonicalResource, modelLabel),
    [canonicalResource, modelLabel],
  );
  // The record projection reads every candidate's fields, whichever implementation the row turns
  // out to be; sections and the rail then show, per record, the children that record admits.
  const sectionEntries = useContainer<React.ReactNode>("form#sections", { models, projection: true });
  const primaryActionFieldEntries = useContainer("form#actions", { models, projection: true });
  const menuActionFieldEntries = useContainer("form#actions-menu", { models, projection: true });
  const chromeFieldEntries = useContainer("form#chrome", { models, projection: true });
  const railEntries = useContainer<React.ReactNode>("form#rail", { models, projection: true });
  const sectionAdmits = useContainerAdmission("form#sections", models, dataResource?.implFields);
  const railAdmits = useContainerAdmission("form#rail", models, dataResource?.implFields);
  const rowDependentSections = React.useMemo(() => rowDependentChildren(sectionEntries), [sectionEntries]);
  // A create form knows no implementation: originals stand and the row-decided children that
  // need one leave the form, fields and all, so their required fields and defaults stay out too.
  const creating = id == null;
  const formSectionEntries = React.useMemo(
    () => creating
      ? sectionEntries.filter((entry) => !rowDependentSections.has(entry.id) || sectionAdmits(entry.id, null))
      : sectionEntries,
    [creating, rowDependentSections, sectionAdmits, sectionEntries],
  );
  React.useEffect(() => {
    if (!developmentMode()) return;
    for (const entry of sectionEntries) {
      for (const child of pageChildren(entry.content as React.ReactNode)) {
        const marker = React.isValidElement(child)
          ? pageElementKind(child.type)
          : null;
        if (marker === "group" || marker === "action" || marker === "tab") {
          continue;
        }
        console.warn(
          `FormView container "${entry.address}" child "${entry.id}" `
            + `has unsupported direct marker "${marker ?? "unmarked"}"; `
            + "only Group, Action, and Tab declarations are discovered.",
        );
      }
    }
  }, [sectionEntries]);
  const contributedDeclarations = React.useMemo(
    () =>
      // Contributed groups and tabs keep the container's order (its sequences and
      // before/after); a non-decreasing sequence interleaves them with the page's own groups at 0.
      formSectionEntries.reduce<{ floor: number; declarations: ContributedFormDeclaration[] }>(({ floor, declarations }, entry, entryOrder) => {
        const sequence = Math.max(floor, entry.sequence ?? 0);
        // A child the row decides (an `impl` child, a variant or its original) shows on the records that admit it.
        const rowDependent = rowDependentSections.has(entry.id);
        return { floor: sequence, declarations: [...declarations,
          ...parsePageGroups(entry.content as React.ReactNode).map(
            (group, childOrder) => ({
              kind: "group" as const,
              // A contribution's permission gates every group and tab it declares.
              group: {
                ...group,
                ...(entry.permission !== undefined ? { permission: entry.permission } : {}),
                ...(rowDependent ? { containerChild: entry.id } : {}),
              },
              sequence,
              order: entryOrder * 1000 + childOrder,
            }),
          ),
          ...parsePageTabs(entry.content as React.ReactNode).map(
            (tab, childOrder) => ({
              kind: "tab" as const,
              tab: entry.permission === undefined && !rowDependent ? tab : { ...tab, visibleWhen: (record: Row) =>
                (entry.permission === undefined || holdsPermission(record, entry.permission))
                && (!rowDependent || sectionAdmits(entry.id, record))
                && (tab.visibleWhen?.(record) ?? true) },
              sequence,
              order: entryOrder * 1000 + childOrder,
            }),
          ),
        ] };
      }, { floor: Number.NEGATIVE_INFINITY, declarations: [] }).declarations,
    [formSectionEntries, rowDependentSections, sectionAdmits],
  );
  const contributedGroups = React.useMemo(
    () =>
      contributedDeclarations.flatMap((declaration) =>
        declaration.kind === "group" ? [declaration.group] : [],
      ),
    [contributedDeclarations],
  );
  const contributedGroupSequences = React.useMemo(
    () =>
      contributedDeclarations.flatMap((declaration) =>
        declaration.kind === "group" ? [declaration.sequence] : [],
      ),
    [contributedDeclarations],
  );
  // Section actions keep the child they came from, so the record decides the row-dependent ones.
  const contributedActions = React.useMemo(
    () =>
      formSectionEntries.flatMap((entry) =>
        parsePageActions(entry.content as React.ReactNode).map((action) => ({
          action,
          ...(rowDependentSections.has(entry.id) ? { containerChild: entry.id } : {}),
        })),
      ),
    [formSectionEntries, rowDependentSections],
  );
  // A canonical section may depend on a parent field omitted by a child's
  // projection. Read that field from its declared owner, never select invalid
  // child fields or require every consumer to duplicate the parent projection.
  const contributionFields = React.useMemo(() => [
    ...contributedDeclarations.flatMap((declaration) => declaration.kind === "tab"
      ? declaration.tab.requiredFields ?? [] : []),
    ...sectionEntries.flatMap((entry) => entry.requiredFields ?? []),
    ...primaryActionFieldEntries.flatMap((entry) => entry.requiredFields ?? []),
    ...menuActionFieldEntries.flatMap((entry) => entry.requiredFields ?? []),
    ...chromeFieldEntries.flatMap((entry) => entry.requiredFields ?? []),
    ...railEntries.flatMap((entry) => entry.requiredFields ?? []),
  ], [chromeFieldEntries, menuActionFieldEntries, primaryActionFieldEntries, railEntries, sectionEntries, contributedDeclarations]);
  const canonicalTabFields = React.useMemo(() => [...new Set(
    contributionFields.filter((path) => {
          const head = path.split(".")[0]!;
          return canonicalResource !== modelLabel
            && !modelMetadata?.fields[head]
            && canonicalMetadata?.fields[head]?.readable !== false
            && Boolean(canonicalMetadata?.fields[head]);
        }),
  )], [canonicalMetadata, canonicalResource, contributionFields, modelLabel, modelMetadata]);
  const isCreate = id == null;
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
  const baseGroups = overrideGroups ?? groups ?? childGroups;
  const declaredGroups = React.useMemo(
    () => [...baseGroups, ...contributedGroups],
    [baseGroups, contributedGroups],
  );
  const declaredGroupSequences = React.useMemo(
    () => [
      ...baseGroups.map(() => 0),
      ...contributedGroupSequences,
    ],
    [baseGroups, contributedGroupSequences],
  );
  const pageActions = overrideActions ?? actions ?? childActions;
  const resolvedFields = React.useMemo(
    () =>
      withModeLockedFields(
        fieldsWithMetadataDefaults(declaredFields, modelMetadata, schemaMetadata),
        isCreate,
      ),
    [declaredFields, isCreate, modelMetadata, schemaMetadata],
  );
  const resolvedGroups = React.useMemo(
    () =>
      declaredGroups.map((group) => ({
        ...group,
        fields: withModeLockedFields(
          fieldsWithMetadataDefaults(group.fields, modelMetadata, schemaMetadata),
          isCreate,
        ),
      })),
    [declaredGroups, isCreate, modelMetadata, schemaMetadata],
  );
  const railGroups = React.useMemo(() => isCreate ? [] : recordRailGroups(railEntries).map((group) => ({
    ...group,
    fields: (group.fields ?? []).map((row) => ({
      ...row,
      field: withModeLockedFields(fieldsWithMetadataDefaults([row.field], modelMetadata), false)[0]!,
    })),
  })), [isCreate, modelMetadata, railEntries]);
  const regularFormFields = React.useMemo(
    () => flattenedFormFields(resolvedFields, resolvedGroups),
    [resolvedFields, resolvedGroups],
  );
  const formFields = React.useMemo(
    () => flattenedFormFields(regularFormFields, railGroups.map((group) => ({
      fields: (group.fields ?? []).map((row) => row.field),
      actions: [],
    }))),
    [regularFormFields, railGroups],
  );
  // `formFields` registers every candidate so any record's values load; one record's form is the
  // groups no row decides plus the row-decided children it admits. Its layout and its save read these.
  const regularFormFieldsFor = React.useCallback(
    (record: Row | null) => flattenedFormFields(resolvedFields, resolvedGroups.filter((group) =>
      group.containerChild === undefined || sectionAdmits(group.containerChild, record))),
    [resolvedFields, resolvedGroups, sectionAdmits],
  );
  const formFieldsFor = React.useCallback(
    (record: Row | null) => flattenedFormFields(regularFormFieldsFor(record), railGroups
      .filter((group) => group.containerChild === undefined || (record != null && railAdmits(group.containerChild, record)))
      .map((group) => ({ fields: (group.fields ?? []).map((row) => row.field), actions: [] }))),
    [railAdmits, railGroups, regularFormFieldsFor],
  );
  const defaultSlugSource = React.useMemo(
    () => formFields.find((field) => field.title)?.name,
    [formFields],
  );
  const fieldByName = React.useMemo(
    () => new Map(formFields.map((field) => [field.name, field])),
    [formFields],
  );
  const hasConditionalFields = React.useMemo(
    () => formFields.some((field) => field.showWhen || field.resolve),
    [formFields],
  );
  const relationByField = React.useMemo(() => {
    const map = new Map<string, RelationFieldInfo>();
    for (const field of formFields) {
      if (field.options) continue;
      const info = relationFieldInfoForDescriptor(field, modelMetadata, schemaMetadata);
      if (info) map.set(field.name, info);
    }
    return map;
  }, [formFields, modelMetadata, schemaMetadata]);
  const selection = React.useMemo(() => {
    const paths = new Set<string>(["id"]);
    if (!isCreate && modelMetadata?.fields.permissions?.readable !== false && modelMetadata?.fields.permissions) {
      paths.add("permissions");
    }
    for (const field of formFields) {
      // Write-only inputs (secrets such as OAuth client_secret) are projected
      // into the artifact with readable=false; render them, never select them.
      const resolved = modelMetadata ? modelFieldForPath(field.name, modelMetadata, schemaMetadata) : null;
      const fieldMetadata = resolved?.field;
      if (modelMetadata && (!fieldMetadata || fieldMetadata.readable === false)) continue;
      // A field that names its own leaf paths reads exactly those, like a column that does.
      if (field.selectionPaths) {
        for (const path of field.selectionPaths) paths.add(path);
        continue;
      }
      // A to-many relation selects its records' identity and representation,
      // the same selection a list column of that relation makes.
      const relationList = modelMetadata && fieldMetadata?.kind === "list"
        ? relationRepresentationForPath(field.name, modelMetadata, schemaMetadata)
        : null;
      if (relationList?.relationList) {
        for (const path of relationList.selectionPaths) paths.add(path);
        continue;
      }
      addFieldSelection(
        paths,
        field,
        relationByField.get(field.name),
        fieldMetadata,
        resolved?.model.resource.query.fields[fieldMetadata?.name ?? field.name],
      );
    }
    const lines = modelMetadata?.resource?.linesResource;
    if (lines?.field) {
      for (const path of lineReadSelectionPaths(lines, schemaMetadata)) {
        paths.add(`${lines.field}.${path}`);
      }
    }
    for (const extra of returning ?? []) paths.add(extra);
    for (const path of contributionFields) {
        if (!canonicalTabFields.includes(path)) paths.add(path);
    }
    const representation = modelMetadata?.resource.recordRepresentation;
    if (representation && modelMetadata.fields[representation]) paths.add(representation);
    // The artifact emits only projected/readable impl columns, so every name is
    // safe to select even when the form does not declare that implementation field.
    for (const field of modelMetadata?.resource?.implFields ?? []) paths.add(field);
    for (const path of Object.values(modelMetadata?.resource?.subtitle ?? {})) {
      if (path) paths.add(path);
    }
    return [...paths];
  }, [canonicalTabFields, contributionFields, formFields, isCreate, modelMetadata, relationByField, returning, schemaMetadata]);
  const refineFields = React.useMemo(
    () => refineFieldsFromPaths(selection),
    [selection],
  );

  const save = useFormViewSave({
    resource,
    id,
    isCreate,
    dataResource,
    modelMetadata,
    formFields,
    formFieldsFor,
    fieldByName,
    refineFields,
    defaultValues,
    acknowledgedSource,
    onSaved,
    submit,
    createSubmit,
    readOnlyWhen,
    onFieldInteractionStart,
    onFieldInteractionCommit,
    onDiscarded,
    defaultSlugSource,
    t,
    readOnly,
  });
  const visibleRailGroups = React.useMemo(
    () => visibleRecordRailGroups(railGroups, save.displayRecord, modelMetadata, railAdmits),
    [railGroups, save.displayRecord, modelMetadata, railAdmits],
  );
  const canonicalTabSelection = React.useMemo(
    () => refineFieldsFromPaths(["id", ...canonicalTabFields]),
    [canonicalTabFields],
  );
  const canonicalRead = useOne({
    resource: refineResourceName(canonicalMetadata?.resource),
    id: id ?? undefined,
    dataProviderName: canonicalMetadata?.resource.schemaName,
    meta: { fields: canonicalTabSelection },
    queryOptions: {
      enabled: !isCreate && canonicalTabFields.length > 0 && Boolean(canonicalMetadata?.resource.roots.detail),
    },
  });
  const tabRecord = React.useMemo(() => save.displayRecord == null ? null : {
    ...(canonicalTabFields.length > 0 ? canonicalRead.result : undefined),
    ...save.displayRecord,
  }, [canonicalRead.result, canonicalTabFields, save.displayRecord]);
  const actionsBlocked = save.formIsDirty || save.pending;
  const chrome = useFormViewRecordChrome({
    dataResource,
    modelLabel,
    canonicalResource,
    id,
    isCreate,
    record: tabRecord,
    formReadOnly: save.formReadOnly,
    actionsBlocked,
  });

  React.useEffect(() => {
    if (recordTab === undefined) setLocalRecordTab(defaultRecordTab);
  }, [defaultRecordTab, resource, id, recordTab]);
  const fieldLayout = React.useMemo(
    () =>
      formViewFieldLayout(
        // Title, status and body come from this record's own fields, never a group it does not show.
        regularFormFieldsFor(save.displayRecord),
        resolvedFields,
        resolvedGroups,
        modelMetadata,
        isCreate,
      ),
    [isCreate, modelMetadata, regularFormFieldsFor, resolvedFields, resolvedGroups, save.displayRecord],
  );
  const declaredActions = React.useMemo(
    () => [
      ...pageActions,
      ...contributedActions.flatMap(({ action, containerChild }) =>
        containerChild === undefined || sectionAdmits(containerChild, save.displayRecord) ? [action] : []),
    ],
    [contributedActions, pageActions, save.displayRecord, sectionAdmits],
  );
  const { titleField, titlePlacementField, statusField, bodyField, gridFields, gridGroups } =
    fieldLayout;
  const titleFieldMessages = titleField
    ? [
        ...fieldErrorMessages(
          save.form.formState.errors[titleField.name]
            ? [save.form.formState.errors[titleField.name]]
            : [],
        ),
      ]
    : [];
  const sections = React.useMemo(
    () => {
      const groupSections = formSections(
        gridFields,
        gridGroups,
        declaredGroupSequences,
        isCreate,
      );
      const permitted = groupSections.filter((section) =>
        (section.permission === undefined
          || (save.displayRecord != null && holdsPermission(save.displayRecord, section.permission)))
        && (section.containerChild === undefined || sectionAdmits(section.containerChild, save.displayRecord)));
      const stacked = permitted.filter((section) => section.label == null);
      const tabbedSections = permitted
        .filter((section) => section.label != null)
        .sort(compareFormSections);
      return [...stacked, ...tabbedSections];
    },
    [declaredGroupSequences, gridFields, gridGroups, isCreate, save.displayRecord, sectionAdmits],
  );
  const subtitleParts = React.useMemo(
    () =>
      recordSubtitleParts(
        save.displayRecord,
        id,
        modelMetadata?.resource?.subtitle,
        t,
      ),
    [id, modelMetadata, save.displayRecord, t],
  );
  const lineRowErrors = React.useMemo(
    () =>
      save.linesActive && save.linesField
        ? lineRowErrorsFromDottedPaths(
            save.serverFieldErrors,
            save.linesField,
          )
        : undefined,
    [save.linesActive, save.linesField, save.serverFieldErrors],
  );
  const recordToolbarContext = React.useMemo<RecordToolbarContext>(
    () => ({
      recordId: id ?? null,
      record: save.displayRecord,
      patchRecord: save.patchRecord,
      reload: save.reload,
      form: save,
    }),
    [id, save],
  );
  const visibleDeleteAction =
    readOnly || deleteAction === undefined ||
    (deleteVisibleWhen !== undefined &&
      (save.displayRecord == null || !deleteVisibleWhen(save.displayRecord)))
      ? undefined
      : deleteAction;
  const recordTabList = React.useMemo(
    () => mergeRecordTabs(
      (recordTabs ?? EMPTY_RECORD_TABS).filter((tab) =>
        !tab.visibleWhen || (tabRecord != null && tab.visibleWhen(tabRecord))),
      contributedDeclarations.flatMap((declaration): RecordTabDescriptor[] => {
        if (declaration.kind !== "tab") return [];
        const tab = declaration.tab;
        if (tab.hidden || (tab.visibleWhen &&
          (tabRecord == null || !tab.visibleWhen(tabRecord)))) return [];
        return [{
          id: tab.id,
          label: tab.label,
          icon: tab.icon,
          badge: tab.badge,
          render: () => tab.children,
        }];
      }),
    ),
    [recordTabs, contributedDeclarations, tabRecord],
  );
  const activeRecordTab = recordTabList.some((tab) => tab.id === requestedRecordTab)
    ? requestedRecordTab
    : overviewHidden && recordTabList[0] ? recordTabList[0].id : FORM_VIEW_OVERVIEW_TAB_ID;
  const pendingFocusRef = React.useRef<{ path: string; recordTabId?: string } | null>(null);
  const [focusRequest, setFocusRequest] = React.useState(0);
  const [requestedFocusPath, setRequestedFocusPath] = React.useState<string | null>(null);
  const focusField = React.useCallback((path: string, options?: RecordFieldFocusOptions) => {
    pendingFocusRef.current = { path, ...options };
    setRequestedFocusPath(path);
    if (options?.recordTabId) setActiveRecordTab(options.recordTabId);
    setFocusRequest((request) => request + 1);
  }, []);
  React.useEffect(() => {
    const pending = pendingFocusRef.current;
    if (!pending || (pending.recordTabId && pending.recordTabId !== activeRecordTab)) return;
    pendingFocusRef.current = null;
    save.form.setFocus(pending.path);
    setRequestedFocusPath(null);
  }, [activeRecordTab, focusRequest, save.form]);
  const recordPanelContext = React.useMemo<RecordPanelContext | null>(
    () =>
      !isCreate && id != null
        ? { recordId: id, reload: save.reload, form: save, focusField }
        : null,
    [focusField, id, isCreate, save],
  );

  return {
    ...save,
    ...chrome,
    t,
    activeRecordTab,
    requestedFocusPath,
    setActiveRecordTab,
    isCreate,
    modelMetadata,
    formFields,
    relationByField,
    hasConditionalFields,
    requiredMessage: t("form.required"),
    titleField,
    titlePlacementField,
    titleFieldMessages,
    statusField,
    bodyField,
    sections,
    railGroups: visibleRailGroups,
    subtitleParts,
    lineRowErrors,
    declaredActions,
    actionsBlocked,
    recordPanelContext,
    recordToolbarContext,
    recordTabList,
    tabbed: recordPanelContext != null && recordTabList.length > 0,
    visibleDeleteAction,
  };
}

type ContributedFormDeclaration =
  | {
      kind: "group";
      group: GroupDescriptor;
      sequence: number;
      order: number;
    }
  | {
      kind: "tab";
      tab: TabDescriptor;
      sequence: number;
      order: number;
    };

function compareFormSections(
  left: FormSectionModel,
  right: FormSectionModel,
): number {
  return (left.sequence ?? 0) - (right.sequence ?? 0)
    || (left.order ?? 0) - (right.order ?? 0);
}


/** Append slot-contributed record tabs after the host-declared ones, fail-fast on id collisions. */
function mergeRecordTabs(
  declared: readonly RecordTabDescriptor[],
  contributed: readonly RecordTabDescriptor[],
): readonly RecordTabDescriptor[] {
  const tabs =
    contributed.length === 0 ? declared : [...declared, ...contributed];
  // The fixed overview tab renders outside this list; its id is reserved.
  const seen = new Set<string>([FORM_VIEW_OVERVIEW_TAB_ID]);
  for (const tab of tabs) {
    if (seen.has(tab.id)) {
      throw new Error(`FormView received duplicate record tab id "${tab.id}".`);
    }
    seen.add(tab.id);
  }
  return tabs;
}
