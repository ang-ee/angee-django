// Data-bound views over the @angee/sdk resource hooks: reusable declarative
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
  type ListEmptyAction,
  type ListEmptyContent,
  type ListEmptyState,
  type ListColumn,
  type ListViewState,
  type ColumnAlign,
} from "./ListView";
export {
  GroupListView,
  type GroupListViewProps,
} from "./GroupListView";
export {
  RowsListView,
  type RowsListViewProps,
} from "./RowsListView";
export {
  RelationPicker,
  type RelationPickerProps,
  type RelationCreateConfig,
} from "./RelationPicker";
export { useEnumOptions, useImplCategory, useImplChoices, useImplPrefill } from "./enum-options";
export {
  GraphView,
  type GraphViewEdge,
  type GraphViewEdgeStyle,
  type GraphViewLayout,
  type GraphViewNode,
  type GraphViewNodeStyle,
  type GraphViewProps,
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
  type FormField,
  type FieldKind,
  type RecordPanelContext,
  type RecordToolbarContext,
  type RecordTabDescriptor,
} from "./FormView";
export {
  DataPage,
  NEW_RECORD_ID,
  type DataPageProps,
  type RecordPlacement,
  type RecordSmartButtonDescriptor,
} from "./DataPage";
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
export { enumValueLabel, statusLabel } from "./ListInternals";
export * from "./data-view-model";
export * from "./data-view-context";
export type { StringIdRow } from "./data-view-surface";
export {
  Action,
  Column,
  Field,
  Group,
  Tab,
  pageChildren,
  pageElementProps,
  parsePageActions,
  parsePageColumns,
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
