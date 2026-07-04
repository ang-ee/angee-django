import * as React from "react";
import {
  rowPublicId,
  type Row,
} from "@angee/metadata";
import { Glyph } from "../chrome/Glyph";
import { cn } from "../lib/cn";
import { ResourceViewSwitcher } from "../toolbars";
import {
  Dialog,
  DialogBackdrop,
  DialogPortal,
  DialogRoot,
} from "../ui/dialog";
import { ControlBandProvider } from "../layouts/ControlBand";
import { DeletePreviewDialog } from "./DeletePreviewDialog";
import {
  ListView,
  type ListColumn,
  type ListViewProps,
  type ResourceListSnapshot,
} from "./ListView";
import { FormView, type FormField, type FormViewProps } from "./FormView";
import type {
  ListComponent,
  ListProps,
} from "./List";
import type { FormProps } from "./Form";
import { RoutedRecordController } from "./resource-routing";
import { useBulkDelete } from "./useBulkDelete";
import {
  ResourceViewProvider,
  useResourceView,
  useResourceViewMaybe,
} from "./resource-view-context";
import {
  type ListViewNavigationScope,
} from "./resource-view-surface";
import {
  stableSerialize,
  type ResourceViewDefaultGroups,
  type ResourceViewGroup,
  type ResourceViewKind,
} from "./resource-view-model";
import {
  parsePageActions,
  parsePageColumns,
  parsePageFacets,
  parsePageFields,
  parsePageGroups,
  mergePageFacets,
  pageChildren,
  pageElementProps,
  requirePageColumns,
  type ActionDescriptor,
  type FacetDescriptor,
  type GroupDescriptor,
} from "./page";
import { RecordPager, type RecordNavigation } from "./RecordPager";

/** Where the open record's form renders relative to the list. */
export type ResourceRecordPlacement = "inline" | "drawer";

/** Record id sentinel that tells `ResourceList` to render a blank create form. */
export const REFINE_CREATE_ID = "new";

export interface RecordSmartButtonDescriptor {
  id: string;
  label: React.ReactNode;
  count: React.ReactNode;
  icon?: string;
  disabled?: boolean;
  onClick?: () => void;
}

export interface ResourceListProps<TRow extends Row = Row> {
  /** Refine/Angee resource id, e.g. `"notes.Note"`, shared by list and form. */
  resource: string;
  /** Columns for the list. Omit when declaring a `List` child. */
  columns?: readonly ListColumn<TRow>[];
  /** Fields for the record form. Omit when declaring a `Form` child. */
  formFields?: readonly FormField[];
  /** Grouped sections for the record form. Omit when declaring a `Form` child. */
  formGroups?: readonly GroupDescriptor[];
  /**
   * Optional `List` and `Form` element declarations parsed by `ResourceList`.
   *
   * `resource` is inherited by nested declarations when omitted, and an explicit
   * nested resource must match. Only one `List` and one `Form` declaration are
   * accepted. Reuse element constants directly; wrapper components hide the
   * marker from the parser.
   */
  children?: React.ReactNode;
  /**
   * Currently open record id; `REFINE_CREATE_ID` (or the `creating` flag) opens a
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
   * record route. `ResourceList` derives the collection base from that child route,
   * reads the active record id when the child is matched, and owns select,
   * create, and close navigation. Do not mix with controlled record props.
   */
  routed?: boolean;
  /** Where the form shows: beside/below the list (`"inline"`) or in a modal. */
  placement?: ResourceRecordPlacement;
  /** List options forwarded to `ListView`. */
  baseFilter?: ListViewProps<TRow>["baseFilter"];
  filterOptions?: ListViewProps<TRow>["filterOptions"];
  facets?: ListViewProps<TRow>["facets"];
  customFilterFields?: ListViewProps<TRow>["customFilterFields"];
  groupOptions?: ListViewProps<TRow>["groupOptions"];
  order?: ListViewProps<TRow>["order"];
  pageSize?: number;
  defaultView?: ResourceViewKind;
  defaultGroup?: ResourceViewGroup | null;
  defaultGroups?: ResourceViewDefaultGroups;
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
  /** Tabs rendered for a saved record beside the form's "Overview" tab (not on
   * create) — e.g. provisioning and chat panels. See `FormView.recordTabs`. */
  recordTabs?: FormViewProps["recordTabs"];
  rowHref?: (row: TRow) => string;
  /** Controls rendered in the list toolbar's leading slot (e.g. a connect button),
   * forwarded to the list. The owning-level alternative to a `ControlBand` sibling. */
  toolbarActions?: ListViewProps<TRow>["toolbarActions"];
  cardActions?: ListViewProps<TRow>["cardActions"];
  className?: string;
}

export type DrawerResourceListProps<TRow extends Row = Row> = Omit<
  ResourceListProps<TRow>,
  "creating" | "onClose" | "onSelect" | "placement" | "recordId" | "routed"
>;

interface ResourceListDeclarations<TRow extends Row = Row> {
  list?: ResourceListDeclaration<TRow>;
  form?: ResourceFormDeclaration;
}

interface ResourceListDeclaration<TRow extends Row = Row> {
  props: ListProps<TRow>;
  columns: readonly ListColumn<TRow>[];
  facets: readonly FacetDescriptor[];
}

interface ResourceFormDeclaration {
  props: FormProps;
  fields: readonly FormField[];
  groups: readonly GroupDescriptor[];
  actions: readonly ActionDescriptor[];
}

/** Internal record-open state and commands resolved before `ResourceListBody`. */
export interface ResourceRecordController<TRow extends Row = Row> {
  recordId?: string | null;
  creating?: boolean;
  onSelect?: (id: string | null) => void;
  onClose?: () => void;
  rowHref?: (row: TRow) => string;
}

/** The refine list action surface, with optional inline/drawer record UX. */
export function ResourceList<TRow extends Row = Row>({
  pageSize,
  defaultView,
  defaultGroup,
  defaultGroups,
  children,
  ...props
}: ResourceListProps<TRow>): React.ReactElement {
  const declarations = parseResourceListDeclarations<TRow>(children);
  validateResourceListDeclarations(
    {
      ...props,
      pageSize,
      defaultView,
      defaultGroup,
      defaultGroups,
    },
    declarations,
  );
  const initialPageSize = declarations.list?.props.pageSize ?? pageSize;
  const initialDefaultView = declarations.list?.props.defaultView ?? defaultView;
  const resourceView = useResourceViewMaybe();
  const initialState = React.useMemo(
    () => ({
      pageSize: initialPageSize,
      view: initialDefaultView,
    }),
    [initialDefaultView, initialPageSize],
  );
  const content = props.routed ? (
    <RoutedRecordController<TRow> newRecordId={REFINE_CREATE_ID}>
      {(recordController) => (
        <ResourceListBody
          {...props}
          pageSize={pageSize}
          defaultView={defaultView}
          defaultGroup={defaultGroup}
          defaultGroups={defaultGroups}
          declarations={declarations}
          recordController={recordController}
        />
      )}
    </RoutedRecordController>
  ) : (
    <ResourceListBody
      {...props}
      pageSize={pageSize}
      defaultView={defaultView}
      defaultGroup={defaultGroup}
      defaultGroups={defaultGroups}
      declarations={declarations}
      recordController={controlledRecordController(props)}
    />
  );

  if (resourceView) {
    return content;
  }

  return (
    <ResourceViewProvider initialState={initialState} resource={props.resource}>
      {content}
    </ResourceViewProvider>
  );
}

/** A drawer-mode `ResourceList` with self-owned record state and inline controls. */
export function DrawerResourceList<TRow extends Row = Row>(
  props: DrawerResourceListProps<TRow>,
): React.ReactElement {
  const [recordId, setRecordId] = React.useState<string | undefined>(undefined);

  return (
    <ControlBandProvider host={undefined}>
      <ResourceList
        {...props}
        placement="drawer"
        recordId={recordId}
        onSelect={(id) => setRecordId(id ?? REFINE_CREATE_ID)}
        onClose={() => setRecordId(undefined)}
      />
    </ControlBandProvider>
  );
}

export interface ResourceFormActionProps
  extends Omit<FormViewProps, "resource" | "id"> {
  resource: string;
  id?: string | null;
}

/** The refine create action surface for one resource. */
export function ResourceCreate({
  resource,
  ...props
}: Omit<ResourceFormActionProps, "id">): React.ReactElement {
  return <FormView {...props} resource={resource} id={null} />;
}

/** The refine edit action surface for one resource record. */
export function ResourceEdit({
  resource,
  id,
  ...props
}: ResourceFormActionProps): React.ReactElement {
  return <FormView {...props} resource={resource} id={id} />;
}

/** The refine show action surface for one resource record. */
export function ResourceShow({
  resource,
  id,
  fields,
  groups,
  ...props
}: ResourceFormActionProps): React.ReactElement {
  return (
    <FormView
      {...props}
      resource={resource}
      id={id}
      fields={fields?.map(readOnlyField)}
      groups={groups?.map(readOnlyGroup)}
    />
  );
}

function readOnlyField(field: FormField): FormField {
  return field.readOnly ? field : { ...field, readOnly: true };
}

function readOnlyGroup(group: GroupDescriptor): GroupDescriptor {
  return {
    ...group,
    fields: group.fields.map(readOnlyField),
  };
}

function controlledRecordController<TRow extends Row>(
  props: ResourceListProps<TRow>,
): ResourceRecordController<TRow> {
  return {
    recordId: props.recordId,
    creating: props.creating,
    onSelect: props.onSelect,
    onClose: props.onClose,
    rowHref: props.rowHref,
  };
}

interface ResourceListBodyProps<TRow extends Row = Row>
  extends ResourceListProps<TRow> {
  declarations: ResourceListDeclarations<TRow>;
  recordController: ResourceRecordController<TRow>;
}

function ResourceListBody<TRow extends Row = Row>({
  resource,
  columns,
  formFields,
  formGroups,
  declarations,
  recordController,
  placement = "inline",
  baseFilter,
  filterOptions,
  facets,
  customFilterFields,
  groupOptions,
  order,
  pageSize,
  defaultView,
  defaultGroup,
  defaultGroups,
  fields,
  list: ListRenderer = ListView as ListComponent<TRow>,
  returning,
  recordSmartButtons = [],
  hideCreate = false,
  createDefaults,
  recordExtras,
  recordTabs,
  toolbarActions,
  cardActions,
  className,
}: ResourceListBodyProps<TRow>): React.ReactElement {
  const resolvedRecordId = recordController.recordId;
  const resolvedCreating =
    Boolean(recordController.creating) || resolvedRecordId === REFINE_CREATE_ID;
  const handleSelectRecord = recordController.onSelect;
  const handleCloseRecord = recordController.onClose;
  const resolvedRowHref = recordController.rowHref;
  const resolvedColumns = declarations.list?.columns ?? requiredColumns(columns);
  const hasRecordSurface =
    declarations.form !== undefined ||
    formFields !== undefined ||
    formGroups !== undefined;
  const resolvedFormFields = declarations.form?.fields ?? formFields;
  const resolvedFormGroups = declarations.form?.groups ?? formGroups;
  const resolvedFormActions = declarations.form?.actions ?? EMPTY_ACTIONS;
  const ResolvedListComponent = declarations.list?.props.list ?? ListRenderer;
  const resolvedFacets = declarations.list
    ? mergePageFacets(facets, declarations.list.facets)
    : facets;
  const listRenderProps = {
    fields,
    baseFilter,
    filterOptions,
    facets: resolvedFacets,
    customFilterFields,
    groupOptions,
    order,
    pageSize,
    defaultView,
    defaultGroup,
    defaultGroups,
    rowHref: resolvedRowHref,
    toolbarActions,
    cardActions,
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
  const resourceView = useResourceView();
  const [listState, setListState] =
    React.useState<ResourceListSnapshot<TRow> | null>(null);
  const [recordNavigationScope, setRecordNavigationScope] =
    React.useState<ListViewNavigationScope | null>(null);
  const [pendingNavigation, setPendingNavigation] =
    React.useState<PendingRecordNavigation | null>(null);
  const listStateRef = React.useRef<ResourceListSnapshot<TRow> | null>(null);

  // A record is open when an id is selected or a create was requested.
  const open = hasRecordSurface && (resolvedCreating || resolvedRecordId != null);
  const editId = resolvedCreating ? null : resolvedRecordId ?? null;
  // Group defaults are forwarded to ListView, the single grouped-capable
  // resource-list surface.
  const handleListStateChange = React.useCallback(
    (next: ResourceListSnapshot<TRow>) => {
      const current = listStateRef.current;
      if (shouldRetainListStateForRecordNavigation({
        current,
        next,
        recordId: !resolvedCreating ? resolvedRecordId : null,
      })) {
        return;
      }
      listStateRef.current = next;
      setListState((current) =>
        listStatesEqual(current, next) ? current : next,
      );
      setRecordNavigationScope((current) =>
        navigationScopesEqual(current, next.navigationScope ?? null)
          ? current
          : (next.navigationScope ?? null),
      );
    },
    [resolvedCreating, resolvedRecordId],
  );
  React.useEffect(() => {
    if (open) return;
    setRecordNavigationScope(null);
    setPendingNavigation(null);
  }, [open]);

  const handleSaved = React.useCallback(
    (row: Row) => {
      const id = rowPublicId(row);
      if (id !== null) handleSelectRecord?.(id);
    },
    [handleSelectRecord],
  );
  const handleCreateRecord = React.useCallback(() => {
    handleSelectRecord?.(null);
  }, [handleSelectRecord]);
  const handleRowClick = React.useCallback(
    (row: TRow) => {
      const id = rowPublicId(row);
      if (id !== null) handleSelectRecord?.(id);
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
    const targetId = rowPublicId(target);
    if (targetId) {
      setPendingNavigation(null);
      handleSelectRecord?.(targetId);
    } else if (listState.rows.length === 0) {
      setPendingNavigation(null);
    }
  }, [handleSelectRecord, listState, pendingNavigation]);

  const setRecordNavigationPage = React.useCallback(
    (page: number) => {
      if (recordNavigationScope) {
        setRecordNavigationScope((current) =>
          current ? { ...current, page } : current,
        );
        return;
      }
      resourceView.setPage(page);
    },
    [resourceView.setPage, recordNavigationScope],
  );

  const recordNavigation = React.useMemo(
    () =>
      buildRecordNavigation({
        creating: resolvedCreating,
        listState,
        recordId: resolvedRecordId,
        onSelect: handleSelectRecord,
        setPage: setRecordNavigationPage,
        setPendingNavigation,
      }),
    [
      handleSelectRecord,
      listState,
      resolvedCreating,
      resolvedRecordId,
      setRecordNavigationPage,
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
  const recordDelete = useBulkDelete(resource, recordDeleteIds, handleRecordDeleted);
  const recordDeleteAction = open && recordDelete.canDelete
    ? {
        canDelete: recordDeleteIds.size > 0,
        isPending: recordDelete.isPending,
        onDelete: recordDelete.deleteInitiate,
      }
    : undefined;
  const recordHeaderActions = open ? (
    <RecordHeaderActions
      view={resourceView.state.view}
      navigation={recordNavigation}
      smartButtons={recordSmartButtons}
      onViewChange={(view) => {
        resourceView.setView(view);
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
      resource={resource}
      columns={resolvedColumns}
      {...listRenderProps}
      onCreate={
        hasRecordSurface && !hideCreate && handleSelectRecord
          ? handleCreateRecord
          : undefined
      }
      onListStateChange={handleListStateChange}
      onRowClick={hasRecordSurface && handleSelectRecord ? handleRowClick : undefined}
    />
  );
  const listStateOnly = open && listState ? (
    <ListStateProbe<TRow>
      list={ResolvedListComponent}
      resource={resource}
      columns={resolvedColumns}
      listRenderProps={listRenderProps}
      navigationScope={recordNavigationScope}
      onListStateChange={handleListStateChange}
    />
  ) : null;

  const recordForm = open ? (
    <FormView
      resource={resource}
      id={editId}
      fields={resolvedFormFields}
      groups={resolvedFormGroups}
      actions={resolvedFormActions}
      {...formRenderProps}
      defaultValues={resolvedCreating ? createDefaults : undefined}
      recordExtras={resolvedCreating ? undefined : recordExtras}
      recordTabs={resolvedCreating ? undefined : recordTabs}
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
    <div className={cn("flex min-h-0 min-w-0 flex-1 flex-col", className)}>
      {open ? (
        <>
          {listStateOnly}
          <div className="overflow-hidden rounded-6 border border-border bg-sheet">
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

function parseResourceListDeclarations<TRow extends Row = Row>(
  children: React.ReactNode,
): ResourceListDeclarations<TRow> {
  let list: ResourceListDeclaration<TRow> | undefined;
  let form: ResourceFormDeclaration | undefined;

  for (const child of pageChildren(children)) {
    if (!React.isValidElement(child)) {
      throw new Error(unrecognizedResourceListChildMessage(child));
    }

    const listProps = pageElementProps<ListProps<TRow>>(child, "list");
    if (listProps) {
      if (list) throw new Error("ResourceList accepts only one List child.");
      list = resourceListDeclaration(listProps);
      continue;
    }

    const formProps = pageElementProps<FormProps>(child, "form");
    if (formProps) {
      if (form) throw new Error("ResourceList accepts only one Form child.");
      form = resourceFormDeclaration(formProps);
      continue;
    }

    throw new Error(unrecognizedResourceListChildMessage(child));
  }

  return {
    ...(list ? { list } : {}),
    ...(form ? { form } : {}),
  };
}

function resourceListDeclaration<TRow extends Row>(
  props: ListProps<TRow>,
): ResourceListDeclaration<TRow> {
  const cached = listDeclarationCache.get(props) as
    | ResourceListDeclaration<TRow>
    | undefined;
  if (cached) return cached;
  const declaration = {
    props,
    columns: requirePageColumns("List", parsePageColumns<TRow>(props.children)),
    facets: mergePageFacets(props.facets, parsePageFacets(props.children)),
  };
  listDeclarationCache.set(props, declaration);
  return declaration;
}

function resourceFormDeclaration(props: FormProps): ResourceFormDeclaration {
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

function validateResourceListDeclarations<TRow extends Row>(
  props: Omit<ResourceListProps<TRow>, "children">,
  declarations: ResourceListDeclarations<TRow>,
): void {
  validateResourceListRouting(props);
  validateNestedResource("List", props.resource, declarations.list?.props.resource);
  validateNestedResource("Form", props.resource, declarations.form?.props.resource);
  if (declarations.list) {
    validateNestedDeclaration({
      owner: "List",
      resourceListProps: props,
      elementProps: declarations.list.props,
      declarationKeys: ["columns"],
      resourceListOwnedKeys: [
        "onCreate",
        "onRowClick",
        "onListStateChange",
      ],
    });
    if (declarations.list.facets.length > 0 && hasOwnDefined(props, "facets")) {
      throw new Error(
        `ResourceList and its List child both declare "facets".`,
      );
    }
  }
  if (declarations.form) {
    validateNestedDeclaration({
      owner: "Form",
      resourceListProps: props,
      elementProps: declarations.form.props,
      declarationKeys: ["formFields", "formGroups"],
      resourceListOwnedKeys: ["id", "onSaved"],
    });
  }
}

function validateResourceListRouting<TRow extends Row>(
  props: Omit<ResourceListProps<TRow>, "children">,
): void {
  if (props.routed) {
    const controlledKeys = ["recordId", "creating", "onSelect", "onClose"];
    const mixed = controlledKeys.filter((key) => hasOwnDefined(props, key));
    if (mixed.length > 0) {
      throw new Error(
        `ResourceList routed mode cannot mix with controlled record props: ${mixed.join(", ")}.`,
      );
    }
    return;
  }
}

function validateNestedDeclaration<TRow extends Row>({
  owner,
  resourceListProps,
  elementProps,
  declarationKeys,
  resourceListOwnedKeys,
}: {
  owner: "List" | "Form";
  resourceListProps: Omit<ResourceListProps<TRow>, "children">;
  elementProps: object;
  declarationKeys: readonly string[];
  resourceListOwnedKeys: readonly string[];
}): void {
  const ownedKeys = new Set(resourceListOwnedKeys);
  for (const key of resourceListOwnedKeys) {
    if (hasOwnDefined(elementProps, key)) {
      throw new Error(`ResourceList owns ${owner} child "${key}" wiring.`);
    }
  }
  for (const key of declarationKeys) {
    if (hasOwnDefined(resourceListProps, key)) {
      throw new Error(
        `ResourceList and its ${owner} child both declare "${key}".`,
      );
    }
  }
  for (const key of Object.keys(elementProps)) {
    if (key === "children" || key === "resource" || ownedKeys.has(key)) continue;
    if (hasOwnDefined(resourceListProps, key)) {
      throw new Error(
        `ResourceList and its ${owner} child both declare "${key}".`,
      );
    }
  }
}

function validateNestedResource(
  owner: string,
  pageResource: string,
  nestedResource: string | undefined,
): void {
  if (!nestedResource || nestedResource === pageResource) return;
  throw new Error(
    `${owner} resource "${nestedResource}" does not match ResourceList resource "${pageResource}".`,
  );
}

function requiredColumns<TRow extends Row>(
  columns: readonly ListColumn<TRow>[] | undefined,
): readonly ListColumn<TRow>[] {
  if (columns) return columns;
  throw new Error("ResourceList requires columns or a List child.");
}

function listElementRenderProps<TRow extends Row>(
  props: ListProps<TRow>,
): Partial<ListViewProps<TRow> & {
  defaultView?: ResourceViewKind;
  defaultGroup?: ResourceViewGroup | null;
  defaultGroups?: ResourceViewDefaultGroups;
}> {
  const {
    children: _children,
    facets: _facets,
    list: _list,
    resource: _model,
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
    resource: _model,
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

function unrecognizedResourceListChildMessage(child: React.ReactNode): string {
  return (
    `ResourceList child ${resourceChildName(child)} is not a List or Form ` +
    "declaration; wrapper components hide the marker from the parser."
  );
}

function resourceChildName(child: React.ReactNode): string {
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
const formDeclarationCache = new WeakMap<object, ResourceFormDeclaration>();

interface PendingRecordNavigation {
  page: number;
  edge: "first" | "last";
}

function ListStateProbe<TRow extends Row>({
  list: ListComponent,
  resource,
  columns,
  listRenderProps,
  navigationScope,
  onListStateChange,
}: {
  list: ListComponent<TRow>;
  resource: string;
  columns: readonly ListColumn<TRow>[];
  listRenderProps: Partial<ListViewProps<TRow>>;
  navigationScope: ListViewNavigationScope | null;
  onListStateChange: (state: ResourceListSnapshot<TRow>) => void;
}): React.ReactElement {
  const content = (
    <ListComponent
      resource={resource}
      columns={columns}
      {...listRenderProps}
      baseFilter={navigationScope?.filter ?? listRenderProps.baseFilter}
      order={navigationScope?.order ?? listRenderProps.order}
      pageSize={navigationScope?.pageSize ?? listRenderProps.pageSize}
      onListStateChange={onListStateChange}
    />
  );
  return (
    <div hidden aria-hidden="true">
      {navigationScope ? (
        <ResourceViewProvider
          key={navigationScopeKey(navigationScope)}
          scope="local"
          resource={resource}
          initialState={{
            filter: navigationScope.filter ?? {},
            page: navigationScope.page,
            pageSize: navigationScope.pageSize,
          }}
        >
          {content}
        </ResourceViewProvider>
      ) : (
        content
      )}
    </div>
  );
}

const EMPTY_RECORD_ID_SET: ReadonlySet<string> = new Set();
const EMPTY_ACTIONS: readonly ActionDescriptor[] = [];

function RecordHeaderActions({
  view,
  navigation,
  smartButtons,
  onViewChange,
}: {
  view: ResourceViewKind;
  navigation: RecordNavigation | null;
  smartButtons: readonly RecordSmartButtonDescriptor[];
  onViewChange: (view: ResourceViewKind) => void;
}): React.ReactElement {
  return (
    <>
      <RecordSmartButtons buttons={smartButtons} />
      {navigation ? <RecordPager navigation={navigation} /> : null}
      <ResourceViewSwitcher
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
    <div className="inline-flex h-btn-md items-stretch gap-px overflow-hidden rounded-6 border border-border-subtle bg-border-subtle">
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

function buildRecordNavigation<TRow extends Row>({
  creating,
  listState,
  recordId,
  onSelect,
  setPage,
  setPendingNavigation,
}: {
  creating: boolean;
  listState: ResourceListSnapshot<TRow> | null;
  recordId?: string | null;
  onSelect?: (id: string | null) => void;
  setPage: (page: number) => void;
  setPendingNavigation: React.Dispatch<
    React.SetStateAction<PendingRecordNavigation | null>
  >;
}): RecordNavigation | null {
  if (creating || typeof recordId !== "string" || !listState) return null;
  const index = listState.rows.findIndex((row) => rowPublicId(row) === recordId);
  if (index < 0) {
    // The open record isn't in the loaded slice (e.g. a grouped list or a deep
    // record). Keep the pager visible with the filtered total; page-local
    // Prev/Next can't resolve neighbors here, so they stay disabled.
    return { total: listState.total ?? listState.rows.length };
  }

  const current = (listState.page - 1) * listState.pageSize + index + 1;
  const total = listState.total ?? Math.max(current, listState.rows.length);
  const prevId = rowPublicId(listState.rows[index - 1]);
  const nextId = rowPublicId(listState.rows[index + 1]);
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

function listStatesEqual<TRow extends Row>(
  left: ResourceListSnapshot<TRow> | null,
  right: ResourceListSnapshot<TRow>,
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
    left.fetching === right.fetching &&
    navigationScopesEqual(
      left.navigationScope ?? null,
      right.navigationScope ?? null,
    )
  );
}

function shouldRetainListStateForRecordNavigation<TRow extends Row>({
  current,
  next,
  recordId,
}: {
  current: ResourceListSnapshot<TRow> | null;
  next: ResourceListSnapshot<TRow>;
  recordId?: string | null;
}): boolean {
  if (!recordId || !current || !next.fetching) return false;
  if (!listStateHasRecord(current, recordId)) return false;
  return !listStateHasRecord(next, recordId);
}

function listStateHasRecord<TRow extends Row>(
  state: ResourceListSnapshot<TRow>,
  recordId: string,
): boolean {
  return state.rows.some((row) => rowPublicId(row) === recordId);
}

function navigationScopesEqual(
  left: ListViewNavigationScope | null,
  right: ListViewNavigationScope | null,
): boolean {
  if (left === right) return true;
  if (!left || !right) return false;
  return (
    left.page === right.page &&
    left.pageSize === right.pageSize &&
    stableSerialize(left.filter ?? null) ===
      stableSerialize(right.filter ?? null) &&
    stableSerialize(left.order ?? null) ===
      stableSerialize(right.order ?? null)
  );
}

function navigationScopeKey(scope: ListViewNavigationScope): string {
  return stableSerialize({
    filter: scope.filter ?? null,
    order: scope.order ?? null,
    page: scope.page,
    pageSize: scope.pageSize,
  });
}

function rowIdsEqual(
  left: readonly Row[],
  right: readonly Row[],
): boolean {
  if (left.length !== right.length) return false;
  return left.every((row, index) => rowPublicId(row) === rowPublicId(right[index]));
}
