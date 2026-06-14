import * as React from "react";
import {
  useResourceList,
  type Row,
} from "@angee/sdk";
import { Glyph } from "../chrome/Glyph";
import { cn } from "../lib/cn";
import { DataViewSwitcher } from "../toolbars";
import { Button } from "../ui/button";
import {
  Dialog,
  DialogBackdrop,
  DialogPortal,
  DialogRoot,
} from "../ui/dialog";
import { DeletePreviewDialog } from "./DeletePreviewDialog";
import {
  ListView,
  type ListColumn,
  type ListViewProps,
  type ListViewState,
} from "./ListView";
import { FormView, type FormField, type FormViewProps } from "./FormView";
import type {
  ListComponent,
  ListProps,
} from "./List";
import type { FormProps } from "./Form";
import { RoutedRecordController } from "./DataPageRouted";
import { useBulkDelete } from "./useBulkDelete";
import {
  DataViewProvider,
  useDataView,
  useDataViewMaybe,
} from "./data-view-context";
import {
  type DataViewFilter,
  type DataViewDefaultGroups,
  type DataViewGroup,
  type DataViewKind,
} from "./data-view-model";
import {
  parsePageActions,
  parsePageColumns,
  parsePageFields,
  parsePageGroups,
  pageChildren,
  pageElementProps,
  requirePageColumns,
  type ActionDescriptor,
  type GroupDescriptor,
} from "./page";

/** Where the open record's form renders relative to the list. */
export type RecordPlacement = "inline" | "drawer";

/** Record id sentinel that tells `DataPage` to render a blank create form. */
export const NEW_RECORD_ID = "new";

export interface RecordSmartButtonDescriptor {
  id: string;
  label: React.ReactNode;
  count: React.ReactNode;
  icon?: string;
  disabled?: boolean;
  onClick?: () => void;
}

export interface DataPageProps<TRow extends Row = Row> {
  /** Model label, e.g. `"notes.Note"`, shared by the list and the form. */
  model: string;
  /** Columns for the list. Omit when declaring a `List` child. */
  columns?: readonly ListColumn<TRow>[];
  /** Fields for the record form. Omit when declaring a `Form` child. */
  formFields?: readonly FormField[];
  /** Grouped sections for the record form. Omit when declaring a `Form` child. */
  formGroups?: readonly GroupDescriptor[];
  /**
   * Optional `List` and `Form` element declarations parsed by `DataPage`.
   *
   * `model` is inherited by nested declarations when omitted, and an explicit
   * nested model must match. Only one `List` and one `Form` declaration are
   * accepted. Reuse element constants directly; wrapper components hide the
   * marker from the parser.
   */
  children?: React.ReactNode;
  /**
   * Currently open record id; `NEW_RECORD_ID` (or the `creating` flag) opens a
   * blank form.
   */
  recordId?: string | null;
  /** True when creating a new record (an alternative to `recordId === null`). */
  creating?: boolean;
  /** Called to open a record (or `null` to start a create). */
  onSelect?: (id: string | null) => void;
  /** Called to dismiss the open record. */
  onClose?: () => void;
  /**
   * Opt into TanStack Router-owned record navigation.
   *
   * In routed mode the collection route must own a nested trailing `$param`
   * record route. `DataPage` derives the collection base from that child route,
   * reads the active record id when the child is matched, and owns select,
   * create, and close navigation. Do not mix with controlled record props.
   */
  routed?: boolean;
  /** Where the form shows: beside/below the list (`"inline"`) or in a modal. */
  placement?: RecordPlacement;
  /** List options forwarded to `ListView`. */
  filter?: ListViewProps<TRow>["filter"];
  filters?: ListViewProps<TRow>["filters"];
  filterFields?: ListViewProps<TRow>["filterFields"];
  groupOptions?: ListViewProps<TRow>["groupOptions"];
  order?: ListViewProps<TRow>["order"];
  pageSize?: number;
  defaultGroup?: DataViewGroup | null;
  defaultGroups?: DataViewDefaultGroups;
  fields?: ListViewProps<TRow>["fields"];
  /** List component used for the collection surface. Defaults to the lean flat list. */
  list?: ListComponent<TRow>;
  /** Form options forwarded to `FormView`. */
  returning?: FormViewProps["returning"];
  /** Host-owned record counters/actions rendered between form actions and views. */
  recordSmartButtons?: readonly RecordSmartButtonDescriptor[];
  /** Hides the built-in "New" button when the host owns creation. */
  hideCreate?: boolean;
  /** Field values seeded into the create form (create only, not edit) — e.g. a
   * filtered list creating rows that match its filter. */
  createDefaults?: Record<string, unknown>;
  /** Custom content rendered below the record form for a saved record (not on
   * create) — e.g. an operator status/provisioning panel. See `FormView.recordExtras`. */
  recordExtras?: FormViewProps["recordExtras"];
  rowHref?: (row: TRow) => string;
  className?: string;
}

interface DataPageDeclarations<TRow extends Row = Row> {
  list?: DataPageListDeclaration<TRow>;
  form?: DataPageFormDeclaration;
}

interface DataPageListDeclaration<TRow extends Row = Row> {
  props: ListProps<TRow>;
  columns: readonly ListColumn<TRow>[];
}

interface DataPageFormDeclaration {
  props: FormProps;
  fields: readonly FormField[];
  groups: readonly GroupDescriptor[];
  actions: readonly ActionDescriptor[];
}

/** Internal record-open state and commands resolved before `DataPageBody`. */
export interface DataPageRecordController<TRow extends Row = Row> {
  recordId?: string | null;
  creating?: boolean;
  onSelect?: (id: string | null) => void;
  onClose?: () => void;
  rowHref?: (row: TRow) => string;
}

/** A collection list with an open-record form for one model. */
export function DataPage<TRow extends Row = Row>({
  pageSize,
  defaultGroup,
  defaultGroups,
  children,
  ...props
}: DataPageProps<TRow>): React.ReactElement {
  const declarations = parseDataPageDeclarations<TRow>(children);
  validateDataPageDeclarations(
    {
      ...props,
      pageSize,
      defaultGroup,
      defaultGroups,
    },
    declarations,
  );
  const initialPageSize = declarations.list?.props.pageSize ?? pageSize;
  const dataView = useDataViewMaybe();
  const initialState = React.useMemo(
    () => ({
      pageSize: initialPageSize,
    }),
    [initialPageSize],
  );
  const content = props.routed ? (
    <RoutedRecordController<TRow> newRecordId={NEW_RECORD_ID}>
      {(recordController) => (
        <DataPageBody
          {...props}
          pageSize={pageSize}
          defaultGroup={defaultGroup}
          defaultGroups={defaultGroups}
          declarations={declarations}
          recordController={recordController}
        />
      )}
    </RoutedRecordController>
  ) : (
    <DataPageBody
      {...props}
      pageSize={pageSize}
      defaultGroup={defaultGroup}
      defaultGroups={defaultGroups}
      declarations={declarations}
      recordController={controlledRecordController(props)}
    />
  );

  if (dataView) {
    return content;
  }

  return (
    <DataViewProvider initialState={initialState} resource={props.model}>
      {content}
    </DataViewProvider>
  );
}

function controlledRecordController<TRow extends Row>(
  props: DataPageProps<TRow>,
): DataPageRecordController<TRow> {
  return {
    recordId: props.recordId,
    creating: props.creating,
    onSelect: props.onSelect,
    onClose: props.onClose,
    rowHref: props.rowHref,
  };
}

interface DataPageBodyProps<TRow extends Row = Row>
  extends DataPageProps<TRow> {
  declarations: DataPageDeclarations<TRow>;
  recordController: DataPageRecordController<TRow>;
}

function DataPageBody<TRow extends Row = Row>({
  model,
  columns,
  formFields,
  formGroups,
  declarations,
  recordController,
  placement = "inline",
  filter,
  filters,
  filterFields,
  groupOptions,
  order,
  pageSize,
  defaultGroup,
  defaultGroups,
  fields,
  list: ListRenderer = ListView as ListComponent<TRow>,
  returning,
  recordSmartButtons = [],
  hideCreate = false,
  createDefaults,
  recordExtras,
  className,
}: DataPageBodyProps<TRow>): React.ReactElement {
  const resolvedRecordId = recordController.recordId;
  const resolvedCreating =
    Boolean(recordController.creating) || resolvedRecordId === NEW_RECORD_ID;
  const handleSelectRecord = recordController.onSelect;
  const handleCloseRecord = recordController.onClose;
  const resolvedRowHref = recordController.rowHref;
  const resolvedColumns = declarations.list?.columns ?? requiredColumns(columns);
  const resolvedFormFields =
    declarations.form?.fields ?? requiredFormFields(formFields);
  const resolvedFormGroups = declarations.form?.groups ?? formGroups;
  const resolvedFormActions = declarations.form?.actions ?? EMPTY_ACTIONS;
  const ResolvedListComponent = declarations.list?.props.list ?? ListRenderer;
  const listRenderProps = {
    fields,
    filter,
    filters,
    filterFields,
    groupOptions,
    order,
    pageSize,
    defaultGroup,
    defaultGroups,
    rowHref: resolvedRowHref,
    ...(declarations.list
      ? listElementRenderProps(declarations.list.props)
      : {}),
  };
  const formRenderProps = {
    returning,
    ...(declarations.form
      ? formElementRenderProps(declarations.form.props)
      : {}),
  };
  const dataView = useDataView();
  const [listState, setListState] =
    React.useState<ListViewState<TRow> | null>(null);
  const [pendingNavigation, setPendingNavigation] =
    React.useState<PendingRecordNavigation | null>(null);

  // A record is open when an id is selected or a create was requested.
  const open = resolvedCreating || resolvedRecordId != null;
  const editId = resolvedCreating ? null : resolvedRecordId ?? null;
  // Group defaults are forwarded to the list component; GroupListView is their
  // sole owner/seeder. The lean ListView ignores them.
  const handleListStateChange = React.useCallback(
    (next: ListViewState<TRow>) => {
      setListState((current) =>
        listStatesEqual(current, next) ? current : next,
      );
    },
    [],
  );

  const handleSaved = React.useCallback(
    (row: Row) => {
      if (typeof row.id === "string") handleSelectRecord?.(row.id);
    },
    [handleSelectRecord],
  );
  const handleCreateRecord = React.useCallback(() => {
    handleSelectRecord?.(null);
  }, [handleSelectRecord]);
  const handleRowClick = React.useCallback(
    (row: TRow) => {
      if (typeof row.id === "string") handleSelectRecord?.(row.id);
    },
    [handleSelectRecord],
  );

  React.useEffect(() => {
    if (!pendingNavigation || !listState || listState.fetching) return;
    if (pendingNavigation.page !== listState.page) return;

    const target =
      pendingNavigation.edge === "first"
        ? listState.rows[0]
        : listState.rows[listState.rows.length - 1];
    const targetId = rowId(target);
    if (targetId) {
      setPendingNavigation(null);
      handleSelectRecord?.(targetId);
    } else if (listState.rows.length === 0) {
      setPendingNavigation(null);
    }
  }, [handleSelectRecord, listState, pendingNavigation]);

  const recordNavigation = React.useMemo(
    () =>
      buildRecordNavigation({
        creating: resolvedCreating,
        listState,
        recordId: resolvedRecordId,
        onSelect: handleSelectRecord,
        setPage: dataView.setPage,
        setPendingNavigation,
      }),
    [
      dataView.setPage,
      handleSelectRecord,
      listState,
      resolvedCreating,
      resolvedRecordId,
    ],
  );

  const recordDeleteIds = React.useMemo<ReadonlySet<string>>(
    () =>
      !resolvedCreating && typeof resolvedRecordId === "string"
        ? new Set([resolvedRecordId])
        : EMPTY_RECORD_ID_SET,
    [resolvedCreating, resolvedRecordId],
  );
  const handleRecordDeleted = React.useCallback(() => {
    handleCloseRecord?.();
  }, [handleCloseRecord]);
  const recordDelete = useBulkDelete(model, recordDeleteIds, handleRecordDeleted);
  const recordDeleteAction = open
    ? {
        canDelete: recordDeleteIds.size > 0,
        isPending: recordDelete.isPending,
        onDelete: recordDelete.deleteInitiate,
      }
    : undefined;
  const recordHeaderActions = open ? (
    <RecordHeaderActions
      view={dataView.state.view}
      navigation={recordNavigation}
      smartButtons={recordSmartButtons}
      onViewChange={(view) => {
        dataView.setView(view);
        handleCloseRecord?.();
      }}
    />
  ) : null;
  const recordDeleteDialog =
    recordDelete.isPreviewOpen && recordDelete.previewState ? (
      <DeletePreviewDialog
        preview={recordDelete.previewState}
        recordCount={recordDelete.previewRecordCount}
        blockedRecordCount={recordDelete.previewBlockedRecordCount}
        overflowCount={recordDelete.previewOverflowCount}
        isPending={recordDelete.isPending}
        onConfirm={recordDelete.onConfirm}
        onCancel={recordDelete.onCancel}
      />
    ) : null;
  const list = (
    <ResolvedListComponent
      model={model}
      columns={resolvedColumns}
      {...listRenderProps}
      onCreate={
        !hideCreate && handleSelectRecord
          ? handleCreateRecord
          : undefined
      }
      onListStateChange={handleListStateChange}
      onRowClick={handleSelectRecord ? handleRowClick : undefined}
    />
  );
  const listStateOnly = open ? (
    <ListStateProbe<TRow>
      model={model}
      columns={resolvedColumns}
      fields={listRenderProps.fields}
      filter={listRenderProps.filter}
      order={listRenderProps.order}
      pageSize={listRenderProps.pageSize}
      dataView={dataView}
      onListStateChange={handleListStateChange}
    />
  ) : null;

  const recordForm = open ? (
    <FormView
      model={model}
      id={editId}
      fields={resolvedFormFields}
      groups={resolvedFormGroups}
      actions={resolvedFormActions}
      {...formRenderProps}
      defaultValues={resolvedCreating ? createDefaults : undefined}
      recordExtras={resolvedCreating ? undefined : recordExtras}
      onSaved={handleSaved}
      toolbarStart={formRenderProps.toolbarStart}
      toolbar={composeNodes(formRenderProps.toolbar, recordHeaderActions)}
      deleteAction={recordDeleteAction}
    />
  ) : null;

  if (placement === "drawer") {
    return (
      <div className={cn("flex flex-col gap-3", className)}>
        {list}
        <DialogRoot
          open={open}
          onOpenChange={(next) => {
            if (!next) handleCloseRecord?.();
          }}
        >
          <DialogPortal>
            <DialogBackdrop />
            <Dialog.Content size="md" className="p-5">
              {recordForm}
            </Dialog.Content>
          </DialogPortal>
        </DialogRoot>
        {recordDeleteDialog}
      </div>
    );
  }

  return (
    <div className={cn("min-w-0", className)}>
      {open ? (
        <>
          {listStateOnly}
          <div className="overflow-hidden rounded-md border border-border bg-sheet">
            {recordForm}
          </div>
          {recordDeleteDialog}
        </>
      ) : (
        list
      )}
    </div>
  );
}

function parseDataPageDeclarations<TRow extends Row = Row>(
  children: React.ReactNode,
): DataPageDeclarations<TRow> {
  let list: DataPageListDeclaration<TRow> | undefined;
  let form: DataPageFormDeclaration | undefined;

  for (const child of pageChildren(children)) {
    if (!React.isValidElement(child)) {
      throw new Error(unrecognizedDataPageChildMessage(child));
    }

    const listProps = pageElementProps<ListProps<TRow>>(child, "list");
    if (listProps) {
      if (list) throw new Error("DataPage accepts only one List child.");
      list = dataPageListDeclaration(listProps);
      continue;
    }

    const formProps = pageElementProps<FormProps>(child, "form");
    if (formProps) {
      if (form) throw new Error("DataPage accepts only one Form child.");
      form = dataPageFormDeclaration(formProps);
      continue;
    }

    throw new Error(unrecognizedDataPageChildMessage(child));
  }

  return {
    ...(list ? { list } : {}),
    ...(form ? { form } : {}),
  };
}

function dataPageListDeclaration<TRow extends Row>(
  props: ListProps<TRow>,
): DataPageListDeclaration<TRow> {
  const cached = listDeclarationCache.get(props) as
    | DataPageListDeclaration<TRow>
    | undefined;
  if (cached) return cached;
  const declaration = {
    props,
    columns: requirePageColumns("List", parsePageColumns<TRow>(props.children)),
  };
  listDeclarationCache.set(props, declaration);
  return declaration;
}

function dataPageFormDeclaration(props: FormProps): DataPageFormDeclaration {
  const cached = formDeclarationCache.get(props);
  if (cached) return cached;
  const declaration = {
    props,
    fields: parsePageFields(props.children),
    groups: parsePageGroups(props.children),
    actions: parsePageActions(props.children),
  };
  formDeclarationCache.set(props, declaration);
  return declaration;
}

function validateDataPageDeclarations<TRow extends Row>(
  props: Omit<DataPageProps<TRow>, "children">,
  declarations: DataPageDeclarations<TRow>,
): void {
  validateDataPageRouting(props);
  validateNestedModel("List", props.model, declarations.list?.props.model);
  validateNestedModel("Form", props.model, declarations.form?.props.model);
  if (declarations.list) {
    validateNestedDeclaration({
      owner: "List",
      dataPageProps: props,
      elementProps: declarations.list.props,
      declarationKeys: ["columns"],
      dataPageOwnedKeys: [
        "onCreate",
        "onRowClick",
        "onListStateChange",
      ],
    });
  }
  if (declarations.form) {
    validateNestedDeclaration({
      owner: "Form",
      dataPageProps: props,
      elementProps: declarations.form.props,
      declarationKeys: ["formFields", "formGroups"],
      dataPageOwnedKeys: ["id", "onSaved"],
    });
  }
}

function validateDataPageRouting<TRow extends Row>(
  props: Omit<DataPageProps<TRow>, "children">,
): void {
  if (props.routed) {
    const controlledKeys = ["recordId", "creating", "onSelect", "onClose"];
    const mixed = controlledKeys.filter((key) => hasOwnDefined(props, key));
    if (mixed.length > 0) {
      throw new Error(
        `DataPage routed mode cannot mix with controlled record props: ${mixed.join(", ")}.`,
      );
    }
    return;
  }
}

function validateNestedDeclaration<TRow extends Row>({
  owner,
  dataPageProps,
  elementProps,
  declarationKeys,
  dataPageOwnedKeys,
}: {
  owner: "List" | "Form";
  dataPageProps: Omit<DataPageProps<TRow>, "children">;
  elementProps: object;
  declarationKeys: readonly string[];
  dataPageOwnedKeys: readonly string[];
}): void {
  const ownedKeys = new Set(dataPageOwnedKeys);
  for (const key of dataPageOwnedKeys) {
    if (hasOwnDefined(elementProps, key)) {
      throw new Error(`DataPage owns ${owner} child "${key}" wiring.`);
    }
  }
  for (const key of declarationKeys) {
    if (hasOwnDefined(dataPageProps, key)) {
      throw new Error(
        `DataPage and its ${owner} child both declare "${key}".`,
      );
    }
  }
  for (const key of Object.keys(elementProps)) {
    if (key === "children" || key === "model" || ownedKeys.has(key)) continue;
    if (hasOwnDefined(dataPageProps, key)) {
      throw new Error(
        `DataPage and its ${owner} child both declare "${key}".`,
      );
    }
  }
}

function validateNestedModel(
  owner: string,
  pageModel: string,
  nestedModel: string | undefined,
): void {
  if (!nestedModel || nestedModel === pageModel) return;
  throw new Error(
    `${owner} model "${nestedModel}" does not match DataPage model "${pageModel}".`,
  );
}

function requiredColumns<TRow extends Row>(
  columns: readonly ListColumn<TRow>[] | undefined,
): readonly ListColumn<TRow>[] {
  if (columns) return columns;
  throw new Error("DataPage requires columns or a List child.");
}

function requiredFormFields(
  fields: readonly FormField[] | undefined,
): readonly FormField[] {
  if (fields) return fields;
  throw new Error("DataPage requires formFields or a Form child.");
}

function listElementRenderProps<TRow extends Row>(
  props: ListProps<TRow>,
): Partial<ListViewProps<TRow> & {
  defaultGroup?: DataViewGroup | null;
  defaultGroups?: DataViewDefaultGroups;
}> {
  const {
    children: _children,
    list: _list,
    model: _model,
    onCreate: _onCreate,
    onRowClick: _onRowClick,
    onListStateChange: _onListStateChange,
    ...forwarded
  } = props;
  return forwarded;
}

function formElementRenderProps(props: FormProps): Partial<FormViewProps> {
  const {
    children: _children,
    id: _id,
    model: _model,
    onSaved: _onSaved,
    ...forwarded
  } = props;
  return forwarded;
}

function hasOwnDefined(object: object, key: string): boolean {
  return (
    Object.prototype.hasOwnProperty.call(object, key) &&
    (object as Record<string, unknown>)[key] !== undefined
  );
}

function composeNodes(
  first: React.ReactNode,
  second: React.ReactNode,
): React.ReactNode {
  if (first == null || first === false) return second ?? null;
  if (second == null || second === false) return first;
  return (
    <>
      {first}
      {second}
    </>
  );
}

function unrecognizedDataPageChildMessage(child: React.ReactNode): string {
  return (
    `DataPage child ${dataPageChildName(child)} is not a List or Form ` +
    "declaration; wrapper components hide the marker from the parser."
  );
}

function dataPageChildName(child: React.ReactNode): string {
  if (React.isValidElement(child)) return elementTypeName(child.type);
  if (typeof child === "string") return `text "${child.trim()}"`;
  return typeof child;
}

function elementTypeName(type: unknown): string {
  if (typeof type === "string") return `<${type}>`;
  if (typeof type === "function") {
    const component = type as { displayName?: string; name?: string };
    return component.displayName ?? component.name ?? "anonymous component";
  }
  if (typeof type === "object" && type !== null) {
    const record = type as { displayName?: string };
    return record.displayName ?? "component";
  }
  return "component";
}

const listDeclarationCache = new WeakMap<object, unknown>();
const formDeclarationCache = new WeakMap<object, DataPageFormDeclaration>();

interface PendingRecordNavigation {
  page: number;
  edge: "first" | "last";
}

function ListStateProbe<TRow extends Row>({
  model,
  columns,
  fields,
  filter,
  order,
  pageSize,
  dataView,
  onListStateChange,
}: {
  model: string;
  columns: readonly ListColumn<TRow>[];
  fields?: ListViewProps<TRow>["fields"];
  filter?: ListViewProps<TRow>["filter"];
  order?: ListViewProps<TRow>["order"];
  pageSize?: number;
  dataView: ReturnType<typeof useDataView>;
  onListStateChange: (state: ListViewState<TRow>) => void;
}): null {
  React.useEffect(() => {
    if (pageSize && dataView.state.pageSize !== pageSize) {
      dataView.setPageSize(pageSize);
    }
  }, [dataView.setPageSize, dataView.state.pageSize, pageSize]);

  const requestedFields = React.useMemo(() => {
    const paths = new Set<string>(["id"]);
    for (const column of columns) paths.add(column.field);
    for (const extra of fields ?? []) paths.add(extra);
    return [...paths];
  }, [columns, fields]);
  const mergedFilter = React.useMemo(
    () => mergeFilters(filter, dataView.state.filter),
    [dataView.state.filter, filter],
  );
  const sortOrder = dataView.state.resourceOrder();
  const list = useResourceList(model, {
    fields: requestedFields,
    filter: mergedFilter,
    order: sortOrder ?? order,
    pageSize: dataView.state.pageSize,
    page: dataView.state.page,
  });
  const rows = list.rows as readonly TRow[];
  const listState = React.useMemo<ListViewState<TRow>>(
    () => ({
      rows,
      total: list.total,
      page: list.page,
      pageSize: list.pageSize,
      pageCount: list.pageCount,
      hasNext: list.hasNext,
      hasPrev: list.hasPrev,
      fetching: list.fetching,
    }),
    [
      rows,
      list.total,
      list.page,
      list.pageSize,
      list.pageCount,
      list.hasNext,
      list.hasPrev,
      list.fetching,
    ],
  );
  React.useEffect(() => {
    onListStateChange(listState);
  }, [listState, onListStateChange]);
  return null;
}

interface RecordNavigation {
  /** Undefined when the open record isn't in the loaded slice (grouped/deep). */
  current?: number;
  total: number;
  onPrev?: () => void;
  onNext?: () => void;
}

const EMPTY_RECORD_ID_SET: ReadonlySet<string> = new Set();
const EMPTY_ACTIONS: readonly ActionDescriptor[] = [];

function RecordHeaderActions({
  view,
  navigation,
  smartButtons,
  onViewChange,
}: {
  view: DataViewKind;
  navigation: RecordNavigation | null;
  smartButtons: readonly RecordSmartButtonDescriptor[];
  onViewChange: (view: DataViewKind) => void;
}): React.ReactElement {
  return (
    <>
      <RecordSmartButtons buttons={smartButtons} />
      {navigation ? <RecordPager navigation={navigation} /> : null}
      <DataViewSwitcher
        view={view}
        ariaLabel="Record view switcher"
        onViewChange={onViewChange}
      />
    </>
  );
}

function RecordSmartButtons({
  buttons,
}: {
  buttons: readonly RecordSmartButtonDescriptor[];
}): React.ReactElement | null {
  if (buttons.length === 0) return null;
  return (
    <div className="inline-flex h-btn-md items-stretch gap-px overflow-hidden rounded-md border border-border-subtle bg-border-subtle">
      {buttons.map((button) => (
        <button
          key={button.id}
          type="button"
          disabled={button.disabled}
          className="inline-flex items-center gap-1.5 bg-sheet px-3 text-xs leading-none text-fg outline-none transition-colors hover:bg-sheet-2 focus-visible:focus-ring disabled:cursor-not-allowed disabled:opacity-60 [&_.glyph]:size-[13px] [&_.glyph]:text-brand"
          onClick={button.onClick}
        >
          <span className="inline-flex items-center gap-1 font-semibold leading-none">
            {button.icon ? <Glyph name={button.icon} /> : null}
            {button.count}
          </span>
          <span className="whitespace-nowrap font-medium text-fg-muted">
            {button.label}
          </span>
        </button>
      ))}
    </div>
  );
}

function RecordPager({
  navigation,
}: {
  navigation: RecordNavigation;
}): React.ReactElement {
  return (
    <nav
      aria-label="Record navigation"
      className="flex items-center gap-2 text-13 text-fg-muted"
    >
      <span className="whitespace-nowrap tabular-nums">
        {navigation.current !== undefined ? (
          <>
            <span className="font-medium text-fg">
              {navigation.current.toLocaleString()}
            </span>{" "}
            / {navigation.total.toLocaleString()}
          </>
        ) : (
          <>/ {navigation.total.toLocaleString()}</>
        )}
      </span>
      <div className="flex items-center gap-1">
        <Button
          type="button"
          variant="ghost"
          size="iconSm"
          aria-label="Previous record"
          disabled={!navigation.onPrev}
          onClick={navigation.onPrev}
        >
          <Glyph name="chevron-left" className="glyph" />
        </Button>
        <Button
          type="button"
          variant="ghost"
          size="iconSm"
          aria-label="Next record"
          disabled={!navigation.onNext}
          onClick={navigation.onNext}
        >
          <Glyph name="chevron-right" className="glyph" />
        </Button>
      </div>
    </nav>
  );
}

function buildRecordNavigation<TRow extends Row>({
  creating,
  listState,
  recordId,
  onSelect,
  setPage,
  setPendingNavigation,
}: {
  creating: boolean;
  listState: ListViewState<TRow> | null;
  recordId?: string | null;
  onSelect?: (id: string | null) => void;
  setPage: (page: number) => void;
  setPendingNavigation: React.Dispatch<
    React.SetStateAction<PendingRecordNavigation | null>
  >;
}): RecordNavigation | null {
  if (creating || typeof recordId !== "string" || !listState) return null;
  const index = listState.rows.findIndex((row) => rowId(row) === recordId);
  if (index < 0) {
    // The open record isn't in the loaded slice (e.g. a grouped list or a deep
    // record). Keep the pager visible with the filtered total; page-local
    // Prev/Next can't resolve neighbors here, so they stay disabled.
    return { total: listState.total ?? listState.rows.length };
  }

  const current = (listState.page - 1) * listState.pageSize + index + 1;
  const total = listState.total ?? Math.max(current, listState.rows.length);
  const prevId = rowId(listState.rows[index - 1]);
  const nextId = rowId(listState.rows[index + 1]);
  const canPrevPage = listState.hasPrev && listState.page > 1;
  const canNextPage =
    listState.hasNext &&
    (listState.total === undefined || current < listState.total);

  return {
    current,
    total,
    onPrev:
      onSelect && prevId
        ? () => onSelect(prevId)
        : onSelect && canPrevPage
          ? () => {
              const page = Math.max(1, listState.page - 1);
              setPendingNavigation({ page, edge: "last" });
              setPage(page);
            }
          : undefined,
    onNext:
      onSelect && nextId
        ? () => onSelect(nextId)
        : onSelect && canNextPage
          ? () => {
              const page = listState.page + 1;
              setPendingNavigation({ page, edge: "first" });
              setPage(page);
            }
          : undefined,
  };
}

function rowId(row: Row | undefined): string | null {
  return typeof row?.id === "string" ? row.id : null;
}

function mergeFilters<TRow extends Row>(
  base: ListViewProps<TRow>["filter"],
  view: DataViewFilter,
): ListViewProps<TRow>["filter"] {
  if (!base) return Object.keys(view).length > 0 ? view : undefined;
  return { ...base, ...view };
}

function listStatesEqual<TRow extends Row>(
  left: ListViewState<TRow> | null,
  right: ListViewState<TRow>,
): boolean {
  if (!left) return false;
  return (
    rowIdsEqual(left.rows, right.rows) &&
    left.total === right.total &&
    left.page === right.page &&
    left.pageSize === right.pageSize &&
    left.pageCount === right.pageCount &&
    left.hasNext === right.hasNext &&
    left.hasPrev === right.hasPrev &&
    left.fetching === right.fetching
  );
}

function rowIdsEqual(
  left: readonly Row[],
  right: readonly Row[],
): boolean {
  if (left.length !== right.length) return false;
  return left.every((row, index) => rowId(row) === rowId(right[index]));
}
