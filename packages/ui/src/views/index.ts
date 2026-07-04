// Rendered resource views over refine/metadata owners: reusable declarative
// list/form views, their collection⇄record page composition, and aggregate
// panels. Hosts configure them with descriptors or with the page element DSL.

export {
  List,
  type ListComponent,
  type ListProps,
} from "./List";
export {
  ListView,
  type ListViewProps,
  type CardActionContext,
  type ListEmptyAction,
  type ListEmptyContent,
  type ListEmptyState,
  type ListColumn,
  type ResourceListSnapshot,
  type ColumnAlign,
} from "./ListView";
export {
  RowsListView,
  type RowsListViewProps,
} from "./RowsListView";
export {
  RelationPicker,
  type RelationPickerProps,
  type RelationCreateConfig,
} from "./RelationPicker";
export {
  MutationDialog,
  type MutationDialogField,
  type MutationDialogProps,
} from "./MutationDialog";
export {
  FieldDescriptorControl,
  type FieldDescriptorControlProps,
} from "./field-descriptor-control";
export { useEnumOptions, useImplCategory, useImplChoices, useImplPrefill } from "./enum-options";
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
export {
  DashboardView,
  type DashboardViewProps,
} from "./DashboardView";
export {
  TreeView,
  type TreeViewProps,
} from "./TreeView";
export {
  ScopedExplorerPane,
  type ScopedExplorerController,
  type ScopedExplorerPaneProps,
  type ScopedExplorerRootPicker,
} from "./ScopedExplorerPane";
export {
  useScopedTreeExplorer,
  type ScopedTreeExplorerController,
  type ScopedTreeExplorerOption,
  type UseScopedTreeExplorerOptions,
} from "./useScopedTreeExplorer";
export {
  GalleryView,
  type GalleryViewProps,
} from "./GalleryView";
export {
  TimelineView,
  type TimelineViewProps,
} from "./TimelineView";
export {
  Notebook,
  type NotebookProps,
} from "./Notebook";
export {
  Tree,
  FolderTree,
  treeVariants,
  type TreeNode,
  type TreeProps,
  type FolderTreeProps,
} from "../ui/tree";
export {
  Metric,
  type MetricProps,
} from "./dashboard/Metric";
export {
  Form,
  type FormProps,
} from "./Form";
export {
  FormView,
  FORM_VIEW_RECORD_CHROME_SLOT,
  FORM_VIEW_SECTIONS_SLOT,
  formViewSectionsSlot,
  type FormViewProps,
  type FormSubmit,
  type FormSubmitContext,
  type FormField,
  type FieldKind,
  type RecordPanelContext,
  type RecordToolbarContext,
  type RecordTabDescriptor,
} from "./FormView";
export { EditableLines, type EditableLinesProps } from "./EditableLines";
export {
  diffLines,
  duplicateLineRow,
  emptyLineRow,
  lineDiffConfig,
  lineToInput,
  recordLinesToRows,
  type LineDiff,
  type LineDiffConfig,
} from "./editable-lines";
export {
  ResourceList,
  ResourceCreate,
  ResourceEdit,
  ResourceShow,
  DrawerResourceList,
  REFINE_CREATE_ID,
  type ResourceListProps,
  type ResourceFormActionProps,
  type DrawerResourceListProps,
  type ResourceRecordPlacement,
  type RecordSmartButtonDescriptor,
} from "./ResourceList";
export { recordPath, useRouteRecordId } from "./resource-routing";
export {
  AggregatePanel,
  type AggregatePanelProps,
  type AggregateDimension,
} from "./AggregatePanel";
export {
  DeletePreviewDialog,
  type DeletePreviewDialogProps,
} from "./DeletePreviewDialog";
export {
  DeletePreviewTree,
  type DeletePreviewTreeProps,
} from "./DeletePreviewTree";
export {
  useBulkDelete,
  type UseBulkDeleteResult,
} from "./useBulkDelete";
export {
  recordActionId,
  useRecordAction,
  useRecordActionMutation,
  type RecordAction,
  type RecordActionRunner,
  type UseRecordActionOptions,
} from "./record-action";
export {
  RecordPager,
  type RecordNavigation,
} from "./RecordPager";
export {
  useRelationFacets,
  type RelationFacets,
  type RelationFacetOptions,
} from "./relation-facet";
export {
  useRelationOptions,
  relationOptionsFromRows,
  relationSelectedOption,
  type RelationOptionsConfig,
  type RelationOptionsList,
  type RelationOptionsResult,
} from "./relation-options";
export * from "./resource-view-model";
export * from "./resource-view-context";
export type { StringIdRow } from "./resource-view-surface";
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
  ActionConfirm,
  ActionContext,
  ActionDescriptor,
  ActionProps,
  ActionResult,
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
