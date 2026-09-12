import * as React from "react";
import { rowPublicId, type Row } from "@angee/metadata";
import { stableSerialize } from "@angee/refine";
import { ControlBandProvider } from "../../../layouts/ControlBand";
import { Workbench } from "../../../layouts/Workbench";
import { cn } from "../../../lib/cn";
import { Dialog, DialogBackdrop, DialogPortal, DialogRoot } from "../../../ui/dialog";
import { DeletePreviewDialog } from "../../tree/DeletePreviewDialog";
import { ListView } from "../ListView";
import { FormView } from "../../form/FormView";
import type { ListComponent } from "../List";
import { useBulkDelete } from "../useBulkDelete";
import { useResourceView } from "../resource-view-context";
import type { CalendarViewSpec } from "../resource-view-types";
import { mergePageFacets } from "../../page";
import { useListRecordNavigation } from "../use-list-record-navigation";
import { composeNodes } from "./child-dsl";
import { formElementRenderProps, listElementRenderProps, mergeCreateDefaults, requiredColumns } from "./declarations";
import { REFINE_CREATE_ID } from "./public";
import type { ResourceListDeclarations, ResourceListProps, ResourceRecordController } from "./public";
import { EMPTY_RECORD_ID_SET, RecordHeaderActions } from "./record-chrome";
interface ResourceListBodyProps<TRow extends Row = Row>
  extends ResourceListProps<TRow> {
  declarations: ResourceListDeclarations<TRow>;
  recordController: ResourceRecordController<TRow>;
}

export function ResourceListBody<TRow extends Row = Row>({
  resource,
  columns,
  formFields,
  formGroups,
  form,
  declarations,
  recordController,
  placement = "inline",
  renderRecord,
  selectFirstRecord = false,
  splitLayout,
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
  calendar,
  laneSource,
  fields,
  list: ListRenderer = ListView as ListComponent<TRow>,
  returning,
  recordSmartButtons = [],
  hideCreate = false,
  createDefaults,
  recordExtras,
  recordTabs,
  recordPresentation,
  defaultRecordTab,
  overviewTab,
  toolbarActions,
  cardActions,
  emptyContent,
  draggableRow,
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
    renderRecord !== undefined ||
    form !== undefined ||
    declarations.form !== undefined ||
    formFields !== undefined ||
    formGroups !== undefined;
  const resolvedFormFields = declarations.form?.fields ?? formFields;
  const resolvedFormGroups = declarations.form?.groups ?? formGroups;
  // A registered form owns its own child declarations. Passing an empty list
  // would be an explicit override and mask its nested <Action> elements.
  const resolvedFormActions = declarations.form?.actions;
  const ResolvedListComponent = declarations.list?.props.list ?? ListRenderer;
  const resolvedFacets = declarations.list
    ? mergePageFacets(facets, declarations.list.facets)
    : facets;
  const resolvedLaneSource = declarations.list?.props.laneSource ?? laneSource;
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
    draggableRow,
    emptyContent,
    laneSource: resolvedLaneSource,
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
  // Collection quick-create seeds live here (the create owner), so calendar
  // range select and board lane create use the same routed form as "New".
  const [quickCreateDefaults, setQuickCreateDefaults] =
    React.useState<Record<string, unknown> | undefined>(undefined);
  const resolvedCreateDefaults = React.useMemo(
    () => mergeCreateDefaults(createDefaults, quickCreateDefaults),
    [createDefaults, quickCreateDefaults],
  );

  // A record is open when an id is selected or a create was requested.
  const open = hasRecordSurface && (resolvedCreating || resolvedRecordId != null);
  const editId = resolvedCreating ? null : resolvedRecordId ?? null;
  const clearSelection = React.useCallback(() => handleSelectRecord?.(null), [handleSelectRecord]);
  const {
    selectRecord,
    retainLocalList,
    navigation: recordNavigation,
    onListStateChange: handleListStateChange,
  } = useListRecordNavigation<TRow>({
    resource,
    navigationScope: recordController.navigationScope,
    recordId:
      open && !resolvedCreating ? resolvedRecordId : null,
    ...(handleSelectRecord ? { onSelect: handleSelectRecord } : {}),
    onSetPage: resourceView.setPage,
    selectFirstRecord,
    firstSelectionKey: stableSerialize(baseFilter ?? null),
    onClearSelection: clearSelection,
  });
  React.useEffect(() => {
    if (open) return;
    setQuickCreateDefaults(undefined);
  }, [open]);

  const handleSaved = React.useCallback(
    (row: Row) => {
      // Creating from a board leaves you on the board: the new card appears in
      // its lane on its own, and selecting the created record instead takes a
      // page whose `onSelect` routes -- a board page usually -- off the board
      // entirely, so the card you just made is the one thing you cannot see.
      // A record surface that stays put (a list's drawer) keeps opening the new
      // record, which is where continuing to edit it belongs.
      if (resolvedCreating && resourceView.state.view === "board") {
        handleCloseRecord?.();
        return;
      }
      const id = rowPublicId(row);
      if (id !== null) handleSelectRecord?.(id);
    },
    [handleCloseRecord, handleSelectRecord, resolvedCreating, resourceView.state.view],
  );
  const handleCreateRecord = React.useCallback(() => {
    handleSelectRecord?.(null);
  }, [handleSelectRecord]);
  const handleCalendarSelectRange = React.useCallback(
    (start: Date, end: Date) => {
      setQuickCreateDefaults(calendar?.createDefaults?.(start, end));
      handleSelectRecord?.(null);
    },
    [calendar, handleSelectRecord],
  );
  const handleBoardCreateInLane = React.useCallback(
    (laneId: string | null) => {
      if (!resolvedLaneSource) return;
      setQuickCreateDefaults({
        [resolvedLaneSource.field]: laneId,
      });
      // Creates omit the lane-local rank so the backend allocates in its true
      // project context. Follow-up if it bites in practice: cross-lane drag
      // midpoints can still collide because lanes are assignee-scoped while
      // rank uniqueness is project-scoped.
      handleSelectRecord?.(null);
    },
    [handleSelectRecord, resolvedLaneSource],
  );
  // The surface-level calendar spec: sources + reschedule from the page, the
  // range-select seam wired to the routed create (only when a create form exists).
  const canQuickCreate = hasRecordSurface && !hideCreate && Boolean(handleSelectRecord);
  const listCalendar = React.useMemo<CalendarViewSpec | undefined>(
    () =>
      calendar
        ? {
            sources: calendar.sources,
            onReschedule: calendar.onReschedule,
            onSelectRange: canQuickCreate ? handleCalendarSelectRange : undefined,
          }
        : undefined,
    [calendar, canQuickCreate, handleCalendarSelectRange],
  );
  const handleRowClick = React.useCallback(
    (row: TRow) => {
      const id = rowPublicId(row);
      if (id !== null) selectRecord(id);
    },
    [selectRecord],
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
      calendar={listCalendar}
      onCreate={
        hasRecordSurface && !hideCreate && handleSelectRecord
          ? handleCreateRecord
          : undefined
      }
      onCreateInLane={
        canQuickCreate && resolvedLaneSource
          ? handleBoardCreateInLane
          : undefined
      }
      onListStateChange={handleListStateChange}
      onRowClick={hasRecordSurface && handleSelectRecord ? handleRowClick : undefined}
    />
  );
  const FormRenderer = form?.Component ?? FormView;
  const recordForm = open ? (
    <FormRenderer
      resource={resource}
      id={editId}
      fields={resolvedFormFields}
      groups={resolvedFormGroups}
      actions={resolvedFormActions}
      {...formRenderProps}
      defaultValues={
        resolvedCreating ? resolvedCreateDefaults : undefined
      }
      recordExtras={resolvedCreating ? undefined : recordExtras}
      recordTabs={resolvedCreating ? undefined : recordTabs}
      recordTab={resolvedCreating ? undefined : recordController.recordTab}
      onRecordTabChange={resolvedCreating ? undefined : recordController.onRecordTabChange}
      recordPresentation={recordPresentation}
      defaultRecordTab={defaultRecordTab}
      overviewTab={overviewTab}
      onSaved={handleSaved}
      toolbarStart={formRenderProps.toolbarStart}
      toolbar={composeNodes(formRenderProps.toolbar, recordHeaderActions)}
      deleteAction={recordDeleteAction}
    />
  ) : null;
  const recordContent = renderRecord && !resolvedCreating
    ? renderRecord({ recordId: editId })
    : recordForm;

  if (placement === "split") {
    return (
      <div className={cn("h-full min-h-0 min-w-0", className)}>
        <Workbench
          primary={<ControlBandProvider host={undefined}>{list}</ControlBandProvider>}
          primarySize={splitLayout?.primarySize}
          contentMinSize={splitLayout?.contentMinSize}
          stackBelow={splitLayout?.stackBelow}
          autoSave={splitLayout?.autoSave}
        >
          {recordContent}
        </Workbench>
        {recordDeleteDialog}
      </div>
    );
  }

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
              {recordContent}
            </Dialog.Content>
          </DialogPortal>
        </DialogRoot>
        {recordDeleteDialog}
      </div>
    );
  }

  return (
    <div
      className={cn("h-full min-h-0 min-w-0", className)}
      data-record-presentation={
        open && !resolvedCreating && recordPresentation === "workspace" && (recordTabs?.length ?? 0) > 0
          ? "workspace"
          : undefined
      }
    >
      <ControlBandProvider inherit={!open} host={undefined}>
        <div hidden={open} aria-hidden={open || undefined} className="h-full min-h-0">
          {!open || retainLocalList ? list : null}
        </div>
      </ControlBandProvider>
      {open ? (
        <>
          <div className="h-full min-h-0 overflow-hidden rounded-6 border border-border bg-sheet">
            {recordContent}
          </div>
          {recordDeleteDialog}
        </>
      ) : null}
    </div>
  );
}
