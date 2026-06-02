// Data-bound views over the @angee/sdk resource hooks: a paginated list, a
// record form, the collection⇄record page that pairs them, and an aggregate
// panel. Each is generic and prop-driven — the host configures the model,
// fields, and columns.

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
} from "./group-list-view";
export {
  FORM_VIEW_NOTEBOOK_SLOT,
  FormView,
  formViewNotebookSlot,
  type FormViewProps,
  type FormField,
  type FieldKind,
} from "./FormView";
export {
  DataPage,
  type DataPageProps,
  type RecordPlacement,
} from "./DataPage";
export {
  AggregatePanel,
  type AggregatePanelProps,
  type AggregateDimension,
} from "./AggregatePanel";
export * from "./data-view-model";
export * from "./data-view-context";
export {
  Action as PageAction,
  Column as PageColumn,
  Field as PageField,
  Group as PageGroup,
  parsePageActions,
  parsePageColumns,
  parsePageFields,
  parsePageGroups,
  PAGE_ELEMENT_SLOT,
} from "./page";
export type {
  ActionConfirm as PageActionConfirm,
  ActionDescriptor as PageActionDescriptor,
  ActionProps as PageActionProps,
  ColumnAggregate as PageColumnAggregate,
  ColumnDescriptor as PageColumnDescriptor,
  ColumnProps as PageColumnProps,
  FieldDescriptor as PageFieldDescriptor,
  FieldProps as PageFieldProps,
  GroupDescriptor as PageGroupDescriptor,
  GroupProps as PageGroupProps,
  PageColumnAlign,
  PageElement,
  PageElementKind,
  PageFieldKind,
} from "./page";
