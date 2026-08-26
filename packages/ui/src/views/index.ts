// Rendered resource views over refine/metadata owners: reusable declarative
// list/form views, their collection⇄record page composition, and aggregate
// panels. Hosts configure them with descriptors or with the page element DSL.

export { List, type ListComponent, type ListProps } from "./resource/List";
export {
  ListView,
  type ListViewProps,
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
export { RowsListView, type RowsListViewProps } from "./resource/RowsListView";
export {
  defineRowAction,
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
  LabeledDescriptorField,
  MutationDialog,
  type MutationDialogControlProps,
  type MutationDialogField,
  type MutationDialogParseValues,
  type MutationDialogProps,
  type MutationDialogRelation,
  type MutationDialogValues,
  mutationDialogValueCodecs,
} from "./form/MutationDialog";
export { RowsField, type RowsValue } from "./form/RowsField";
export {
  deserializeFormSpec,
  formSpecInitialValues,
  useFormSpecFields,
  type FormSpecFieldDescriptor,
  type FormSpecFieldType,
  type FormSpecRelationCreate,
} from "./form/form-spec";
export {
  ActionFormDialog,
  type ActionFormDialogProps,
} from "./form/ActionFormDialog";
export {
  useActionForm,
  type UseActionFormOptions,
  type UseActionFormResult,
} from "./form/use-action-form";
export {
  useDottedPathFieldErrors,
  lineRowErrorsFromDottedPaths,
  validationErrorMap,
  validationErrorsFromError,
  type DottedPathFieldErrorMap,
  type DottedPathFieldErrors,
  type ValidationErrors,
} from "./form/validation-errors";
export {
  FieldDescriptorControl,
  type FieldDescriptorControlProps,
} from "./form/field-descriptor-control";
export {
  useEnumOptions,
  useImplCategory,
  useImplChoices,
  useImplPrefill,
} from "./relation/enum-options";
export {
  GraphView,
  type GraphViewEdge,
  type GraphViewEdgeStyle,
  type GraphViewLayout,
  type GraphViewNode,
  type GraphViewNodeStyle,
  type GraphViewProps,
  type GraphViewConnection,
  type GraphViewPosition,
} from "./GraphView";
export { DashboardView, type DashboardViewProps } from "./dashboard/DashboardView";
export { TreeView, type TreeViewProps } from "./tree/TreeView";
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
  FormView,
  FORM_VIEW_RECORD_ACTIONS_SLOT,
  FORM_VIEW_RECORD_CHROME_SLOT,
  FORM_VIEW_SECTIONS_SLOT,
  formViewRecordActionsSlot,
  formViewSectionsSlot,
  type FormViewProps,
  type FormSubmit,
  type FormSubmitContext,
  type FormField,
  type FieldKind,
  type RecordPanelContext,
  type RecordToolbarContext,
  type RecordTabDescriptor,
} from "./form/FormView";
export {
  RecordChromeProvider,
  useRecordChromeContext,
  type RecordChromeContext,
} from "./resource/record-chrome-context";
export { EditableLines, type EditableLinesProps } from "./form/EditableLines";
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
  type ResourceListCalendarSpec,
  type ResourceFormActionProps,
  type DrawerResourceListProps,
  type ResourceRecordPlacement,
  type RecordSmartButtonDescriptor,
} from "./resource/ResourceList";
export { useRouteRecordId } from "./resource/resource-routing";
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
export { useBulkDelete, type UseBulkDeleteResult } from "./resource/useBulkDelete";
export {
  recordActionId,
  useActionResultMutation,
  useRecordAction,
  useRecordActionMutation,
  useRecordChromeActionMutation,
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
  relationOptionsFromRows,
  relationSelectedOption,
  type RelationOptionsConfig,
  type RelationOptionsList,
  type RelationOptionsResult,
} from "./relation/relation-options";
export * from "./resource/resource-view-model";
export * from "./resource/resource-view-context";
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
  ActionConfirm,
  ActionContext,
  ActionDescriptor,
  ActionFormContext,
  ActionProps,
  ActionRelationArg,
  ActionRelationListArg,
  ActionResult,
  ActionScalarArg,
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
