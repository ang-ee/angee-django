import * as React from "react";
import { type Row } from "@angee/metadata";
import { ControlBandProvider } from "../../../layouts/ControlBand";
import { type ListColumn, type ListViewProps } from "../ListView";
import { FormView, type FormField, type FormViewProps } from "../../form/FormView";
import type { ListComponent, ListProps } from "../List";
import type { FormProps } from "../../form/Form";
import { RoutedRecordController } from "../resource-routing";
import { ResourceViewProvider, useResourceViewMaybe } from "../resource-view-context";
import { type ResourceViewDefaultGroups, type ResourceViewGroup, type ResourceViewKind } from "../resource-view-model";
import type { BoardLaneSource } from "../resource-view-types";
import type { Occurrence } from "../../calendar/CalendarView";
import type { AnyCalendarWindowSource } from "../../calendar/use-calendar-window";
import { type ActionDescriptor, type FacetDescriptor, type GroupDescriptor } from "../../page";
import { ResourceListBody } from "./body";
import { parseResourceListDeclarations, validateResourceListDeclarations } from "./declarations";
/** Where the open record's form renders relative to the list. */
export type ResourceRecordPlacement = "inline" | "drawer";

/**
 * The calendar declaration a resource page hands `ResourceList`: the occurrence
 * sources, the reschedule handler, and how a selected range seeds the create form.
 * `ResourceList` owns the routed-create wiring — a range select seeds the form
 * defaults and opens create through the same seam as the "New" button.
 */
export interface ResourceListCalendarSpec {
  /** Occurrence sources fetched per visible window and merged onto the grid. */
  sources: readonly AnyCalendarWindowSource[];
  /** Persist an editable occurrence's drag/resize; reject to revert the grid. */
  onReschedule?: (
    occurrence: Occurrence,
    start: Date,
    end: Date | null,
  ) => void | Promise<unknown>;
  /** Map a selected range to the create form's seed values (quick-create). */
  createDefaults?: (start: Date, end: Date) => Record<string, unknown>;
}

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
  /** Calendar sources + interaction seams. When declared, the Calendar kind is
   * offered in the switcher and rendered as a windowed-collection surface;
   * quick-create rides `ResourceList`'s routed-create seam. */
  calendar?: ResourceListCalendarSpec;
  /** Declared board lanes for a relation group field; empty lanes render too. */
  laneSource?: BoardLaneSource;
  fields?: ListViewProps<TRow>["fields"];
  /** List component used for the collection surface. Defaults to the lean flat list. */
  list?: ListComponent<TRow>;
  /** Form options forwarded to `FormView`. */
  returning?: FormViewProps["returning"];
  /** Host-owned record counters/actions rendered between form actions and views. */
  recordSmartButtons?: readonly RecordSmartButtonDescriptor[];
  /** Hides the built-in "New" button when the host owns creation. */
  hideCreate?: boolean;
  /** List-scope create seed (create only, not edit): field values a filtered list
   * seeds new rows with so they match its active filter/facet. This is the
   * facet-seed owner and forwards to `FormView.defaultValues`. A *fixed per-field*
   * create default the form itself owns belongs on `Field.defaultValue` instead —
   * which, unlike this prop, also submits when the field is `readOnly`/`createOnly`. */
  createDefaults?: Record<string, unknown>;
  /** Custom content rendered below the record form for a saved record (not on
   * create) — e.g. an operator status/provisioning panel. See `FormView.recordExtras`. */
  recordExtras?: FormViewProps["recordExtras"];
  /** Tabs rendered for a saved record beside the form's "Overview" tab (not on
   * create) — e.g. provisioning and chat panels. See `FormView.recordTabs`. */
  recordTabs?: FormViewProps["recordTabs"];
  rowHref?: (row: TRow) => string;
  /** Native cross-pane drag payload forwarded to supported record-row renderers. */
  draggableRow?: ListViewProps<TRow>["draggableRow"];
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

export interface ResourceListDeclarations<TRow extends Row = Row> {
  list?: ResourceListDeclaration<TRow>;
  form?: ResourceFormDeclaration;
}

export interface ResourceListDeclaration<TRow extends Row = Row> {
  props: ListProps<TRow>;
  columns: readonly ListColumn<TRow>[];
  facets: readonly FacetDescriptor[];
}

export interface ResourceFormDeclaration {
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
