// Rendered resource views over refine/metadata owners: reusable declarative
// list/form views, their collection⇄record page composition, and aggregate
// panels. Hosts configure them with descriptors or with the page element DSL.
export {
  RecordFieldMarksProvider, RecordFieldMarkButton, useRecordFieldMarks, useActiveRecordForm, useRevealedRecordField,
  type RecordFieldMark, type ActiveRecordForm,
} from "./form/record-field-marks";

export { SchemaPathPicker, type SchemaPathPickerProps, type SchemaPath, type SchemaPathSchema } from "./SchemaPathPicker";
export { useFormHistory, type FormHistory } from "./form/use-form-history";
export {
  keyedCollectionFromRecord,
  createKeyedEntry,
  keyedCollectionToRecord,
  type KeyedEntry,
  type KeyedCollection,
  type KeyedCollectionSnapshot,
} from "./form/keyed-collection";

export { List, type ListComponent, type ListProps } from "./resource/List";
export {
  ListView,
  type ListViewProps,
  type BoardCardSpec,
  type ListChrome,
  type CardActionContext,
  type ListEmptyAction,
  type ListEmptyContent,
  type ListEmptyState,
  type ListColumn,
  type ListViewNavigationScope,
  type ResourceListSnapshot,
  type ColumnAlign,
} from "./resource/ListView";
export {
  useListRecordNavigation,
  type UseListRecordNavigationOptions,
  type UseListRecordNavigationResult,
} from "./resource/use-list-record-navigation";
export {
  RECORD_TAB_SEARCH_KEY,
  parseRecordNavigationScope,
  recordNavigationSearch,
  recordNavigationHref,
  recordTargetHref,
  recordTargetSearch,
  routeSearchParam,
  updateRouteSearch,
} from "./resource/record-navigation-context";
export type { RecordTargetSearch } from "./resource/record-navigation-context";
export { RowsListView, type RowsListViewProps } from "./resource/RowsListView";
export {
  collectionQuery,
  type CollectionSource,
  type CollectionPage,
  type CollectionPageRequest,
  type CollectionGroupRequest,
} from "./resource/collection-source";
export {
  defineRowAction,
  useDescriptorRowActions,
  rowIdVariables,
  type AuthoredRowActionDeclaration,
  type PageRowActionDeclaration,
  type RowActionConfirmCopy,
  type RowActionDeclaration,
  type RowActionPendingPolicy,
  type RowActionToastCopy,
  type TypedAuthoredRowActionDeclaration,
  type TypedPageRowActionDeclaration,
} from "./resource/RowActions";
export {
  RelationPicker,
  type RelationPickerProps,
  type RelationCreateConfig,
} from "./relation/RelationPicker";
export {
  MutationDialog,
  type MutationDialogParseValues,
  type MutationDialogProps,
  type MutationDialogValues,
  mutationDialogValueCodecs,
} from "./form/MutationDialog";
export { RowsField, type RowsValue } from "./form/RowsField";
export {
  deserializeFormSpec,
  formSpecInitialValues,
  formSpecHasControlForPath,
  normalizeFormSpecValues,
  resolveSchemaReference,
  useFormSpecFields,
  type FormSpecFieldDescriptor,
  type FormSpecFieldType,
  type FormSpecRelationCreate,
} from "./form/form-spec";
export {
  FORM_SPEC_ANNOTATIONS,
  parseFormSpec,
  parseFormSpecPayload,
  type FormSpecWire,
} from "./form/form-spec-schema";
export { structuredFieldErrorPaths, textValue } from "./form/field-values";
export { useUnsavedChangesNavigationGuard } from "./form/use-unsaved-changes-navigation-guard";
export {
  ActionFormDialog,
  type ActionFormDialogProps,
} from "./form/ActionFormDialog";
export { RecordActionBar } from "./form/RecordActionBar";
export { useWatch, useFormState, type ResolverResult } from "react-hook-form";
export {
  useActionForm,
  type UseActionFormOptions,
  type UseActionFormResult,
} from "./form/use-action-form";
export { ActionFormProvider } from "./form/ActionFormProvider";
export {
  actionFormSubmitResult,
  actionOutcomeSubmitResult,
  invalidFormSubmit,
  formSubmitError,
  savedFormSubmitResult,
  applyFormErrors,
  type FormSubmitResult,
  useDottedPathFieldErrors,
  directDottedPathMessages,
  lineRowErrorsFromDottedPaths,
  messagesForDottedPath,
  validationErrorMessages,
  validationErrorMap,
  validationErrorsFromError,
  type DottedPathFieldErrorMap,
  type DottedPathFieldErrors,
  type ValidationErrors,
} from "./form/validation-errors";
export {
  DescriptorFieldList,
  LabeledDescriptorField,
  type DescriptorFieldListProps,
  type DescriptorField,
  type DescriptorFieldRelation,
  type DescriptorFieldControlProps,
} from "./form/DescriptorFieldList";
export { fieldErrorMessages, isCompositeFieldDescriptor } from "./form/form-view-model";
export {
  FieldDescriptorControl,
  type FieldDescriptorControlProps,
} from "./form/field-descriptor-control";
export {
  useEnumOptions,
  useImplCategory,
  useImplConfigFields,
  useImplChoices,
  type ImplConfigFields,
  useImplPrefill,
} from "./relation/enum-options";
export {
  GraphView,
  graphNodeStyle,
  type GraphViewActivation,
  type GraphViewEdge,
  type GraphViewEdgeStyle,
  type GraphViewLayout,
  type GraphViewInitialView,
  type GraphViewNode,
  type GraphViewNodeStyle,
  type GraphViewProps,
  type GraphViewConnection,
  type GraphViewPosition,
  type GraphViewPort,
  type GraphViewStatus,
} from "./GraphView";
export {
  GraphEditor,
  type GraphEditorNode,
  type GraphEditorLink,
  type GraphEditorLayout,
  type GraphEditorSelection,
  type GraphEditorNodeAction,
  type GraphEditorProps,
} from "./GraphEditor";
export { layoutGraph, findFreeGraphPosition, placeGraphNodeBeside } from "./graph-layout";
export {
  DashboardView,
  type DashboardViewProps,
} from "./dashboard/DashboardView";
export { TreeView, type TreeViewProps } from "./tree/TreeView";
export {
  RelationFieldWidget,
  type RelationFieldWidgetProps,
} from "./relation/RelationFieldWidget";
export { RecordReference, type RecordReferenceProps } from "./relation/RecordReference";
export {
  CollectionTreeView,
  type CollectionTreeViewProps,
} from "./tree/CollectionTreeView";
export {
  ScopedExplorerPane,
  type ScopedExplorerController,
  type ScopedExplorerPaneProps,
  type ScopedExplorerRootPicker,
} from "./tree/ScopedExplorerPane";
export {
  useScopedTreeExplorer,
  type ScopedTreeExplorerController,
  type ScopedTreeExplorerOption,
  type UseScopedTreeExplorerOptions,
} from "./tree/useScopedTreeExplorer";
export { GalleryView, type GalleryViewProps } from "./GalleryView";
export { TimelineView, type TimelineViewProps } from "./TimelineView";
export {
  CalendarView,
  type CalendarViewProps,
  type CalendarViewMode,
  type CalendarWindow,
  type Occurrence,
} from "./calendar/CalendarView";
export {
  useCalendarWindow,
  calendarWindowBounds,
  calendarWindowSource,
  type AnyCalendarWindowSource,
  type CalendarWindowBounds,
  type CalendarWindowSource,
  type UseCalendarWindowResult,
} from "./calendar/use-calendar-window";
export {
  CalendarCollectionSurface,
  type CalendarCollectionSurfaceProps,
  type CalendarWindowFetch,
} from "./calendar/calendar-collection-surface";
export {
  Tree,
  FolderTree,
  treeVariants,
  type TreeNode,
  type TreeProps,
  type FolderTreeProps,
} from "../ui/tree";
export { Metric, type MetricProps } from "./dashboard/Metric";
export { Form, type FormProps } from "./form/Form";
export {
  RegisteredFormView,
  registerForm,
  useRegisteredForm,
  type RegisteredForm,
  type RegisteredFormProps,
} from "./form/registered-form";
export {
  FormView,
  SectionHeading,
  RecordRailGroup,
  acknowledgeFormSubmit,
  FORM_CONTAINERS,
  type FormViewProps,
  type SectionHeadingProps,
  type RecordRailField,
  type RecordRailGroupProps,
  type FormSubmit,
  type FormSubmitAcknowledgement,
  type FormSubmitContext,
  type FormViewAcknowledgedSource,
  type FormField,
  type FieldKind,
  type OverviewTabOptions,
  type RecordPresentation,
  type RecordPanelContext,
  type RecordToolbarContext,
  type RecordTabDescriptor,
} from "./form/FormView";
export { type RecordActionDescriptor } from "./form/RecordActionBar";
export {
  RecordChrome,
  RecordChromeProvider,
  useRecordChromeContext,
  useRecordChromeContextMaybe,
  type RecordChromeContext,
} from "./resource/record-chrome-context";
export {
  EditableLines,
  type EditableLineSupplementalColumn,
  type EditableLinesProps,
} from "./form/EditableLines";
export {
  BoundDescriptorField,
  BoundFormValue,
  useFormViewValues,
  type BoundDescriptorFieldProps,
  type BoundFormValueProps,
  type BoundFormValueRenderProps,
} from "./form/BoundDescriptorField";
export { useListIdentities } from "./form/StructuredField";
export {
  diffLines,
  duplicateLineRow,
  emptyLineRow,
  lineDiffConfig,
  lineToInput,
  recordLinesToRows,
  type LineDiff,
  type LineDiffConfig,
} from "./form/editable-lines";
export {
  ResourceList,
  ResourceCreate,
  ResourceEdit,
  ResourceShow,
  DrawerResourceList,
  REFINE_CREATE_ID,
  type ResourceListProps,
  type ListCreateAction,
  type ResourceRecordRenderContext,
  type ResourceListSplitLayout,
  type ResourceListCalendarSpec,
  type ResourceFormActionProps,
  type DrawerResourceListProps,
  type ResourceRecordPlacement,
  type RecordSmartButtonDescriptor,
} from "./resource/ResourceList";
export {
  useRouteParam,
  useRouteRecordId,
  useRouteSearch,
} from "./resource/resource-routing";
export {
  AggregatePanel,
  type AggregatePanelProps,
  type AggregateDimension,
} from "./resource/AggregatePanel";
export {
  DeletePreviewDialog,
  type DeletePreviewDialogProps,
} from "./tree/DeletePreviewDialog";
export {
  DeletePreviewTree,
  type DeletePreviewTreeProps,
} from "./tree/DeletePreviewTree";
export {
  useBulkDelete,
  type UseBulkDeleteResult,
} from "./resource/useBulkDelete";
export {
  useDeletePreviewOperation,
  useDeleteWithPreview,
  type ResourceOperation,
  type UseDeleteWithPreviewResult,
} from "./resource/resource-operations";
export {
  useActionOutcomeMutation,
  useActionResultMutation,
  useRecordAction,
  useRecordActionMutation,
  useRecordChromeActionMutation,
  useRecordChromeActionOutcome,
  type ActionResultMutation,
  type RecordAction,
  type RecordActionRunner,
  type UseActionResultMutationOptions,
  type UseRecordActionOptions,
  type UseRecordChromeActionMutationOptions,
} from "./resource/record-action";
export { useAuthoredResourceMutation } from "./resource/authored-resource-mutation";
export {
  useActionResultRun,
  type ActionResultRun,
  type ActionResultRunOptions,
} from "./resource/action-result-run";
export { RecordPager, type RecordNavigation } from "./resource/RecordPager";
export {
  useRelationFacets,
  type RelationFacets,
  type RelationFacetOptions,
} from "./relation/relation-facet";
export {
  useRelationOptions,
  useRelationSelectedOption,
  relationOptionsFromRows,
  relationSelectedOption,
  type RelationOptionsConfig,
  type RelationOptionsList,
  type RelationOptionsResult,
} from "./relation/relation-options";
export * from "./resource/resource-view-model";
export * from "./resource/resource-view-context";
export {
  ResourceViewUtilities,
  useResourceViewUtilities,
  useResourceViewUtilityContext,
  type ResourceViewUtilityContext,
} from "./resource/resource-view-utilities";
export type { StringIdRow } from "./resource/resource-view-surface";
export {
  Action,
  Column,
  Facet,
  Field,
  Group,
  Tab,
  mergePageFacets,
  pageChildren,
  pageElementProps,
  parsePageActions,
  parsePageColumns,
  parsePageFacets,
  parsePageFields,
  parsePageGroups,
  parsePageTabs,
  PAGE_ELEMENT_SLOT,
} from "./page";
export type {
  ActionArg,
  ActionArgs,
  ActionConfirm,
  ActionContext,
  ActionDescriptor,
  ActionFormContext,
  ActionFormDefinition,
  ActionProps,
  ActionRelationArg,
  ActionRelationListArg,
  ActionResult,
  ActionScalarArg,
  ActionSubmitResult,
  ColumnAggregate,
  ColumnDescriptor,
  ColumnProps,
  FacetDescriptor,
  FacetProps,
  FieldDescriptor,
  FieldProps,
  GroupDescriptor,
  GroupProps,
  PageColumnAlign,
  PageElement,
  PageElementKind,
  PageFieldKind,
  TabDescriptor,
  TabProps,
} from "./page";

export {
  ManageAccessDialog, type ManageAccessDialogProps, type RecordAccessEntry,
  type AccessPerson, type AccessRole, type AccessVisibility,
} from "./access/ManageAccessDialog";
export { SubjectPicker, type SubjectPickerProps } from "./access/SubjectPicker";

export { GanttView, type GanttViewProps, type GanttEvent, type GanttResource, type GanttScale } from "./gantt/GanttView";
export { GanttLane, type GanttLaneDetails, type GanttLanePerson } from "./gantt/gantt-lane";
export type { GanttViewSpec } from "./resource/resource-view-types";
export {
  RESOURCE_CONTAINERS,
  ResourceViewKindsProvider,
  useOfferedResourceViewKinds,
  useResourceViewKindContent,
  useResourceViewKinds,
} from "./resource/resource-view-kinds";
