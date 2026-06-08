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
  Tab,
  type NotebookProps,
  type TabProps,
} from "./Notebook";
export {
  Tree,
  FolderTree,
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
  type FormViewProps,
  type FormField,
  type FieldKind,
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
export * from "./data-view-model";
export * from "./data-view-context";
export type { StringIdRow } from "./data-view-surface";
export {
  Action,
  Column,
  Field,
  Group,
  pageChildren,
  pageElementProps,
  parsePageActions,
  parsePageColumns,
  parsePageFields,
  parsePageGroups,
  PAGE_ELEMENT_SLOT,
} from "./page";
export type {
  ActionConfirm,
  ActionDescriptor,
  ActionProps,
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
} from "./page";
