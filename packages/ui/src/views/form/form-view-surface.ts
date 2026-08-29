import * as React from "react";
import {
  lineReadSelectionPaths,
  useModelMetadata,
  useSchemaFieldMetadata,
  type ModelMetadata,
  type Row,
} from "@angee/metadata";
import { refineFieldsFromPaths } from "@angee/refine";

import { useFormOverride, useModelSlot } from "../../runtime";
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
  type ActionDescriptor,
  type FieldDescriptor,
  type GroupDescriptor,
  type TabDescriptor,
} from "../page";
import {
  fieldsWithMetadataDefaults,
  relationFieldInfo,
  type RelationFieldInfo,
} from "../resource/model-metadata-defaults";
import type { RecordDeleteAction } from "./RecordActionBar";
import { formViewSectionsSlot } from "./form-view-slots";
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
  type FormViewSaveSurface,
} from "./use-form-view-save";

export type { FormSubmit, FormSubmitContext } from "./use-form-view-save";

/** Value of the form body's leading tab, shown when record tabs are declared. */
export const FORM_VIEW_OVERVIEW_TAB_ID = "overview";

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

export interface RecordTabDescriptor {
  id: string;
  label: React.ReactNode;
  icon?: React.ReactNode;
  render: (context: RecordPanelContext) => React.ReactNode;
  /**
   * Keep the panel mounted even while inactive. Base UI mounts a keep-mounted
   * panel eagerly from the first render, so do not opt in for eager work such as
   * a socket, subscription, fetch, or token mint; leave it false unless state
   * preservation outweighs that cost.
   */
  keepMounted?: boolean;
}

export interface UseFormViewSurfaceProps {
  resource: string;
  id?: string | null;
  fields?: readonly FieldDescriptor[];
  groups?: readonly GroupDescriptor[];
  children?: React.ReactNode;
  actions?: readonly ActionDescriptor[];
  returning?: readonly string[];
  defaultValues?: Record<string, unknown>;
  onSaved?: (row: Row) => void;
  submit?: FormSubmit;
  createSubmit?: FormSubmit;
  recordTabs?: readonly RecordTabDescriptor[];
  deleteAction?: RecordDeleteAction;
  deleteVisibleWhen?: (record: Row) => boolean;
}

export interface FormViewSurface
  extends FormViewSaveSurface,
    FormViewRecordChromeSurface {
  t: UiTranslate;
  activeRecordTab: string;
  setActiveRecordTab: React.Dispatch<React.SetStateAction<string>>;
  isCreate: boolean;
  modelMetadata: ModelMetadata | null;
  formFields: readonly FieldDescriptor[];
  relationByField: ReadonlyMap<string, RelationFieldInfo>;
  hasConditionalFields: boolean;
  requiredMessage: string;
  titleField: FieldDescriptor | undefined;
  titleFieldMessages: readonly string[];
  statusField: FieldDescriptor | undefined;
  bodyField: FieldDescriptor | undefined;
  sections: readonly FormSectionModel[];
  subtitleParts: readonly React.ReactNode[];
  lineRowErrors: readonly (ValidationErrors | undefined)[] | undefined;
  declaredActions: readonly ActionDescriptor[];
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
  fields,
  groups,
  children,
  actions,
  returning,
  defaultValues,
  onSaved,
  submit,
  createSubmit,
  recordTabs,
  deleteAction,
  deleteVisibleWhen,
}: UseFormViewSurfaceProps): FormViewSurface {
  const t = useUiT();
  const [activeRecordTab, setActiveRecordTab] = React.useState(
    FORM_VIEW_OVERVIEW_TAB_ID,
  );
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
  const formOverride = useFormOverride(modelLabel);
  const sectionTarget = React.useMemo(
    () => formViewSectionsSlot(modelLabel),
    [modelLabel],
  );
  const sectionEntries = useModelSlot(sectionTarget);
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
          `FormView slot "${sectionTarget.slot}" contribution "${entry.id}" `
            + `has unsupported direct marker "${marker ?? "unmarked"}"; `
            + "only Group, Action, and Tab declarations are discovered.",
        );
      }
    }
  }, [sectionEntries, sectionTarget.slot]);
  const slotDeclarations = React.useMemo(
    () =>
      sectionEntries.flatMap((entry, entryOrder) => {
        const sequence = entry.sequence ?? 0;
        return [
          ...parsePageGroups(entry.content as React.ReactNode).map(
            (group, childOrder) => ({
              kind: "group" as const,
              group,
              sequence,
              order: entryOrder * 1000 + childOrder,
            }),
          ),
          ...parsePageTabs(entry.content as React.ReactNode).map(
            (tab, childOrder) => ({
              kind: "tab" as const,
              tab,
              sequence,
              order: entryOrder * 1000 + childOrder,
            }),
          ),
        ];
      }).sort(compareSlotDeclaration),
    [sectionEntries],
  );
  const slotGroups = React.useMemo(
    () =>
      slotDeclarations.flatMap((declaration) =>
        declaration.kind === "group" ? [declaration.group] : [],
      ),
    [slotDeclarations],
  );
  const slotGroupSequences = React.useMemo(
    () =>
      slotDeclarations.flatMap((declaration) =>
        declaration.kind === "group" ? [declaration.sequence] : [],
      ),
    [slotDeclarations],
  );
  const slotTabs = React.useMemo(
    () =>
      slotDeclarations.flatMap((declaration) =>
        declaration.kind === "tab" ? [declaration] : [],
      ),
    [slotDeclarations],
  );
  const slotActions = React.useMemo(
    () =>
      sectionEntries.flatMap((entry) =>
        parsePageActions(entry.content as React.ReactNode),
      ),
    [sectionEntries],
  );
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
    () => [...baseGroups, ...slotGroups],
    [baseGroups, slotGroups],
  );
  const declaredGroupSequences = React.useMemo(
    () => [
      ...baseGroups.map(() => 0),
      ...slotGroupSequences,
    ],
    [baseGroups, slotGroupSequences],
  );
  const declaredActions = React.useMemo(
    () => [...(overrideActions ?? actions ?? childActions), ...slotActions],
    [actions, childActions, overrideActions, slotActions],
  );
  const resolvedFields = React.useMemo(
    () =>
      withModeLockedFields(
        fieldsWithMetadataDefaults(declaredFields, modelMetadata),
        isCreate,
      ),
    [declaredFields, isCreate, modelMetadata],
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
    [declaredGroups, isCreate, modelMetadata],
  );
  const formFields = React.useMemo(
    () => flattenedFormFields(resolvedFields, resolvedGroups),
    [resolvedFields, resolvedGroups],
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
    () => formFields.some((field) => field.showWhen),
    [formFields],
  );
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
      if (modelMetadata && !modelMetadata.fields[field.name]) continue;
      addFieldSelection(
        paths,
        field,
        relationByField.get(field.name),
        modelMetadata?.fields[field.name],
      );
    }
    const lines = isCreate ? null : modelMetadata?.resource?.linesResource;
    if (lines?.field) {
      for (const path of lineReadSelectionPaths(lines, schemaMetadata)) {
        paths.add(`${lines.field}.${path}`);
      }
    }
    for (const extra of returning ?? []) paths.add(extra);
    const representation = modelMetadata?.recordRepresentation;
    if (representation && modelMetadata.fields[representation]) paths.add(representation);
    // The artifact emits only projected/readable impl columns, so every name is
    // safe to select even when the form does not declare that implementation field.
    for (const field of modelMetadata?.resource?.implFields ?? []) paths.add(field);
    for (const path of Object.values(modelMetadata?.resource?.subtitle ?? {})) {
      if (path) paths.add(path);
    }
    return [...paths];
  }, [formFields, isCreate, modelMetadata, relationByField, returning, schemaMetadata]);
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
    fieldByName,
    refineFields,
    defaultValues,
    onSaved,
    submit,
    createSubmit,
    defaultSlugSource,
    t,
  });
  const chrome = useFormViewRecordChrome({
    dataResource,
    modelLabel,
    canonicalResource,
    id,
    isCreate,
    record: save.displayRecord,
  });

  React.useEffect(() => {
    setActiveRecordTab(FORM_VIEW_OVERVIEW_TAB_ID);
  }, [resource, id]);
  const fieldLayout = React.useMemo(
    () =>
      formViewFieldLayout(
        formFields,
        resolvedFields,
        resolvedGroups,
        modelMetadata,
      ),
    [formFields, modelMetadata, resolvedFields, resolvedGroups],
  );
  const { titleField, statusField, bodyField, gridFields, gridGroups } =
    fieldLayout;
  const titleFieldMessages = titleField
    ? [
        ...fieldErrorMessages(
          save.form.formState.errors[titleField.name]
            ? [save.form.formState.errors[titleField.name]]
            : [],
        ),
        ...(save.serverFieldErrors[titleField.name] ?? []),
      ]
    : [];
  const sections = React.useMemo(
    () => {
      const groupSections = formSections(
        gridFields,
        gridGroups,
        declaredGroupSequences,
      );
      const tabSections = chrome.recordChromeContext == null
        ? []
        : slotTabs.flatMap(({ tab, sequence, order }) =>
            tab.hidden
              ? []
              : [formSectionFromTab(tab, sequence, order)],
          );
      const stacked = groupSections.filter((section) => section.label == null);
      const tabbedSections = [
        ...groupSections.filter((section) => section.label != null),
        ...tabSections,
      ].sort(compareFormSections);
      return [...stacked, ...tabbedSections];
    },
    [chrome.recordChromeContext, declaredGroupSequences, gridFields, gridGroups, slotTabs],
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
  const recordPanelContext = React.useMemo<RecordPanelContext | null>(
    () =>
      !isCreate && id != null
        ? { recordId: id, reload: save.reload }
        : null,
    [id, isCreate, save.reload],
  );
  const recordToolbarContext = React.useMemo<RecordToolbarContext>(
    () => ({
      recordId: id ?? null,
      record: save.displayRecord,
      patchRecord: save.patchRecord,
      reload: save.reload,
    }),
    [id, save.displayRecord, save.patchRecord, save.reload],
  );
  const visibleDeleteAction =
    deleteAction === undefined ||
    (deleteVisibleWhen !== undefined &&
      (save.displayRecord == null || !deleteVisibleWhen(save.displayRecord)))
      ? undefined
      : deleteAction;
  const recordTabList = recordTabs ?? EMPTY_RECORD_TABS;

  return {
    ...save,
    ...chrome,
    t,
    activeRecordTab,
    setActiveRecordTab,
    isCreate,
    modelMetadata,
    formFields,
    relationByField,
    hasConditionalFields,
    requiredMessage: t("form.required"),
    titleField,
    titleFieldMessages,
    statusField,
    bodyField,
    sections,
    subtitleParts,
    lineRowErrors,
    declaredActions,
    recordPanelContext,
    recordToolbarContext,
    recordTabList,
    tabbed: recordPanelContext != null && recordTabList.length > 0,
    visibleDeleteAction,
  };
}

type SlotFormDeclaration =
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

function compareSlotDeclaration(
  left: SlotFormDeclaration,
  right: SlotFormDeclaration,
): number {
  return left.sequence - right.sequence || left.order - right.order;
}

function formSectionFromTab(
  tab: TabDescriptor,
  sequence: number,
  order: number,
): FormSectionModel {
  return {
    key: `tab:${tab.id}`,
    label: tab.label,
    icon: tab.icon,
    badge: tab.badge,
    fields: [],
    render: () => tab.children,
    sequence,
    order,
  };
}

function compareFormSections(
  left: FormSectionModel,
  right: FormSectionModel,
): number {
  return (left.sequence ?? 0) - (right.sequence ?? 0)
    || (left.order ?? 0) - (right.order ?? 0);
}

function developmentMode(): boolean {
  const viteEnv = (
    import.meta as ImportMeta & { readonly env?: { readonly DEV?: boolean } }
  ).env;
  if (typeof viteEnv?.DEV === "boolean") return viteEnv.DEV;
  const nodeEnv = (
    globalThis as typeof globalThis & {
      process?: { env?: { NODE_ENV?: string } };
    }
  ).process?.env?.NODE_ENV;
  return nodeEnv !== "production";
}
