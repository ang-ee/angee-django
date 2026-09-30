import * as React from "react";
import { useModelMetadata } from "@angee/metadata";

import { Button } from "../../ui/button";
import { Tabs } from "../../ui/tabs";
import { useBreadcrumbLeafLabel } from "../../chrome/Breadcrumb";
import { renderGlyph } from "../../chrome/Glyph";
import { ControlBand, ControlBandProvider } from "../../layouts/ControlBand";
import { cn } from "../../lib/cn";
import { SlotOutlet } from "../../lib/slot-outlet";
import { ErrorBanner } from "../../fragments/ErrorBanner";
import { EmptyState } from "../../fragments/EmptyState";
import { Skeleton, SkeletonStatus } from "../../ui/skeleton";
import {
  RecordChrome,
  RecordChromeProvider,
} from "../resource/record-chrome-context";
import { RecordActionBar } from "./RecordActionBar";
import type {
  FieldDescriptor,
  PageFieldKind,
} from "../page";
import {
  FORM_VIEW_OVERVIEW_TAB_ID,
  useFormViewSurface,
  type RecordPanelContext,
  type RecordTabDescriptor,
  type OverviewTabOptions,
  type RecordPresentation,
  type RecordToolbarContext,
  type UseFormViewSurfaceProps,
} from "./form-view-surface";
import {
  FORM_VIEW_COLUMN_CLASS,
  FormViewOverview,
  FormViewRail,
  FormViewRecordHeader,
} from "./form-view-body";
import type { EditableLineSupplementalColumn, EditableLinesProps } from "./EditableLines";
import { recordRepresentationValue, titleText } from "./form-view-model";
import { useRuntimeViewAs } from "../../runtime";
import { useAppRuntime } from "../../runtime";
import { resolveTabLabel } from "../page";
import { SectionHeading } from "./SectionHeading";
import { RecordRailGroup } from "./form-view-rail";
import { formViewRailSlot } from "./form-view-slots";

export { SectionHeading, type SectionHeadingProps } from "./SectionHeading";
export { RecordRailGroup, type RecordRailField, type RecordRailGroupProps } from "./form-view-rail";

export {
  acknowledgeFormSubmit,
} from "./form-view-surface";

export {
  FORM_VIEW_RECORD_ACTIONS_SLOT,
  FORM_VIEW_RECORD_CHROME_SLOT,
  FORM_VIEW_RAIL_SLOT,
  FORM_VIEW_SECTIONS_SLOT,
  formViewRailSlot,
  formViewRecordActionsSlot,
  formViewSectionsSlot,
} from "./form-view-slots";

export type FieldKind = PageFieldKind;
export type FormField = FieldDescriptor;

export type {
  FormSubmit,
  FormSubmitAcknowledgement,
  FormSubmitContext,
  FormViewAcknowledgedSource,
  OverviewTabOptions,
  RecordPresentation,
  RecordPanelContext,
  RecordFieldFocusOptions,
  RecordTabDescriptor,
  RecordToolbarContext,
} from "./form-view-surface";

export interface FormViewProps extends UseFormViewSurfaceProps {
  /** Suppress contributed record toolbar controls in a passive embedded peek. */
  hideRecordChrome?: boolean;
  /** Publish this routed record's representation into the current breadcrumb. */
  publishBreadcrumbLabel?: boolean;
  /** Override the record heading from the same live create/edit form context. */
  title?: React.ReactNode | ((context: RecordToolbarContext) => React.ReactNode);
  submitLabel?: React.ReactNode;
  toolbarStart?:
    | React.ReactNode
    | ((context: RecordToolbarContext) => React.ReactNode);
  toolbar?: React.ReactNode;
  /** Saved-record content rendered below, but outside, the form element. */
  recordExtras?: (context: RecordPanelContext) => React.ReactNode;
  /** Non-form content rendered after the overview fields for both create and edit. */
  formExtras?: (context: RecordToolbarContext) => React.ReactNode;
  /** Compact read-only content rendered with the record heading. */
  headerExtras?: (context: RecordToolbarContext) => React.ReactNode;
  /** One concise domain context line under the record title. */
  contextLine?: (context: RecordToolbarContext) => React.ReactNode;
  /** Group presentation; ungrouped/title/body/status placement is unchanged. */
  layout?: "stacked" | "tabs";
  /** Place the first two unlabeled groups side by side within one form overview. */
  groupLayout?: "stacked" | "paired";
  /** Content tabs inside the one create/edit form, alongside its editable lines and groups. */
  bodyTabs?: readonly {
    id: string;
    label: React.ReactNode;
    render: (context: RecordToolbarContext) => React.ReactNode;
  }[];
  linesTabLabel?: React.ReactNode;
  /** Editable line fields shown before the user expands advanced line details. */
  linePrimaryFields?: readonly string[];
  /** Read-only domain projections rendered beside editable line fields. */
  lineSupplementalColumns?: readonly EditableLineSupplementalColumn[];
  /** Domain-owned filters applied to relation pickers on editable lines. */
  lineRelationFilters?: EditableLinesProps["relationFilters"];
  /** Record chrome density and height behavior. */
  recordPresentation?: RecordPresentation;
  /** Initial saved-record tab; invalid or unavailable ids fall back to Overview. */
  defaultRecordTab?: string;
  /** Label and bounded placement of the built-in form tab. */
  overviewTab?: OverviewTabOptions;
  className?: string;
}

/**
 * Thin form shell. `useFormViewSurface` owns declarations through submit/diff;
 * the section owners below only bind that view model to shared UI primitives.
 */
function FormViewComponent(props: FormViewProps): React.ReactElement {
  const model = useModelMetadata(props.resource);
  const identity = `${model?.resource?.schemaName ?? "default"}:${model?.resource?.modelLabel ?? props.resource}:${props.id ?? "create"}`;
  return <FormViewInstance
    key={identity}
    {...props}
  />;
}

/** Existing public FormView export also exposes form-owned declaration seams. */
export const FormView = Object.assign(FormViewComponent, {
  RailGroup: RecordRailGroup,
  SectionHeading,
  railSlot: formViewRailSlot,
});

function FormViewInstance(props: FormViewProps): React.ReactElement {
  const { i18n } = useAppRuntime();
  const preview = useRuntimeViewAs();
  const previewBlocked = Boolean(preview.viewAs || preview.pending);
  const {
    resource,
    id,
    readOnly = false,
    fields,
    groups,
    children,
    actions,
    returning,
    defaultValues,
    acknowledgedSource,
    onSaved,
    submit,
    createSubmit,
    readOnlyWhen,
    onFieldInteractionStart,
    onFieldInteractionCommit,
    onDiscarded,
    recordTabs,
    recordTab,
    onRecordTabChange,
    deleteAction,
    deleteVisibleWhen,
    submitLabel,
    toolbarStart,
    toolbar,
    title,
    recordExtras,
    formExtras,
    headerExtras,
    contextLine,
    layout = "stacked",
    groupLayout = "stacked",
    bodyTabs,
    linesTabLabel,
    linePrimaryFields,
    lineSupplementalColumns,
    lineRelationFilters,
    recordPresentation = "document",
    defaultRecordTab,
    overviewTab,
    publishBreadcrumbLabel = false,
    className,
  } = props;
  const surface = useFormViewSurface({
    resource,
    id,
    readOnly,
    fields,
    groups,
    children,
    actions,
    returning,
    defaultValues,
    acknowledgedSource,
    onSaved,
    submit,
    createSubmit,
    readOnlyWhen,
    onFieldInteractionStart,
    onFieldInteractionCommit,
    onDiscarded,
    recordTabs,
    recordTab,
    onRecordTabChange,
    defaultRecordTab,
    deleteAction,
    deleteVisibleWhen,
  });
  const {
    t,
    activeRecordTab,
    setActiveRecordTab,
    isCreate,
    formReadOnly,
    formIsDirty,
    displayRecord,
    saveError,
    staleRevision,
    declaredActions,
    actionsBlocked,
    recordChromeContext,
    recordActions,
    recordPanelContext,
    recordToolbarContext,
    recordTabList,
    tabbed,
    visibleDeleteAction,
    loading,
    pending,
    submitForm,
    discardChanges,
    applyPatch,
    reload,
  } = surface;
  const saveErrorBanner = <ErrorBanner description={saveError} title={t("form.saveFailed")}
    actions={staleRevision ? <Button type="button" size="sm" variant="secondary" disabled={pending}
      onClick={() => { discardChanges(); reload(); }}>
      {t("form.reloadStaleRevision")}
    </Button> : undefined}
  />;
  const primaryRecordActions = React.useMemo(
    () => recordActions.filter((entry) => entry.recordActionPlacement !== "menu"),
    [recordActions],
  );
  const menuRecordActions = React.useMemo(
    () => recordActions.filter((entry) => entry.recordActionPlacement === "menu"),
    [recordActions],
  );
  useBreadcrumbLeafLabel(
    titleText(
      recordRepresentationValue(displayRecord, surface.modelMetadata),
      "",
    ) || null,
    publishBreadcrumbLabel && !isCreate,
  );
  const awaitingRecord = !isCreate && displayRecord == null && loading;
  if (!isCreate && !awaitingRecord && displayRecord == null) {
    return <EmptyState icon="lock" title={t("form.recordUnavailable")} className="min-h-64 p-8" />;
  }
  const toolbarStartNode =
    typeof toolbarStart === "function"
      ? toolbarStart(recordToolbarContext)
      : toolbarStart;
  const overview = awaitingRecord ? (
    <SkeletonStatus label={t("form.loading")} className="grid gap-4 py-5">
      <Skeleton shape="text" className="h-6 w-2/3" />
      <Skeleton className="h-32 w-full" />
    </SkeletonStatus>
  ) : (
    <FormViewOverview
      surface={surface} layout={layout} groupLayout={groupLayout} bodyTabs={bodyTabs}
      linesTabLabel={linesTabLabel} linePrimaryFields={linePrimaryFields}
      lineSupplementalColumns={lineSupplementalColumns}
      lineRelationFilters={lineRelationFilters} context={recordToolbarContext}
    />
  );
  const overviewLabel = overviewTab?.label ?? t("form.tabOverview");
  const orderedTabs = overviewTab?.position === "last"
    ? [...recordTabList, { id: FORM_VIEW_OVERVIEW_TAB_ID, label: overviewLabel }]
    : [{ id: FORM_VIEW_OVERVIEW_TAB_ID, label: overviewLabel }, ...recordTabList];
  const recordExtrasPanel =
    !awaitingRecord && recordPanelContext && recordExtras ? (
      <div className={cn(FORM_VIEW_COLUMN_CLASS, "pb-12")}>
        {recordExtras(recordPanelContext)}
      </div>
    ) : null;
  const withRail = (body: React.ReactNode, active: boolean, workspace = false) => <div className={cn("@container w-full", workspace && "h-full min-h-0")}>
    <div className={cn("grid gap-6", workspace && "min-h-full grid-rows-[minmax(0,1fr)_auto] @min-[52rem]:h-full @min-[52rem]:grid-rows-none", active && surface.railGroups.length > 0 && "@min-[52rem]:grid-cols-[minmax(0,1fr)_14rem]")}>
      <div className={cn("min-w-0", workspace && "min-h-0")}>{body}</div>
      {active ? <FormViewRail surface={surface} /> : null}
    </div>
  </div>;
  const overviewContent = withRail(<>
    {overview}
    {!awaitingRecord && formExtras ? <div className="pt-2">{formExtras(recordToolbarContext)}</div> : null}
  </>, !tabbed || activeRecordTab === FORM_VIEW_OVERVIEW_TAB_ID);
  const overviewWithFormExtras = recordChromeContext
    ? <RecordChromeProvider value={recordChromeContext}>{overviewContent}</RecordChromeProvider>
    : overviewContent;
  const renderRecordPanel = (tab: RecordTabDescriptor) => {
    if (!recordPanelContext) return null;
    const content = withRail(tab.render(recordPanelContext), activeRecordTab === tab.id, recordPresentation === "workspace");
    return recordChromeContext
      ? <RecordChromeProvider value={recordChromeContext}>{content}</RecordChromeProvider>
      : content;
  };
  const formTitle = typeof title === "function" ? title(recordToolbarContext) : title;
  const headerExtra = awaitingRecord ? undefined : headerExtras?.(recordToolbarContext);
  // A declared context line replaces the generic subtitle even when it says nothing for this record.
  const headerContextLine = awaitingRecord ? undefined : contextLine ? (contextLine(recordToolbarContext) ?? <></>) : undefined;
  const recordHeader = (compact = false) => {
    const header = <FormViewRecordHeader surface={surface} compact={compact} awaiting={awaitingRecord} title={formTitle}
      extra={headerExtra} contextLine={headerContextLine} />;
    return recordChromeContext
      ? <RecordChromeProvider value={recordChromeContext}>{header}</RecordChromeProvider>
      : header;
  };

  const handleFormKeyDown = (event: React.KeyboardEvent<HTMLFormElement>) => {
    const titleEditor = event.target instanceof HTMLTextAreaElement
      && event.target.dataset.formTitle === "true";
    if (
      (!isCreate && !titleEditor) ||
      event.key !== "Enter" ||
      event.defaultPrevented ||
      event.nativeEvent.isComposing ||
      (!titleEditor && (!(event.target instanceof HTMLInputElement) || event.target.type !== "text"))
    ) {
      return;
    }
    event.preventDefault();
    void submitForm();
  };
  const controlBand = readOnly && props.hideRecordChrome && !toolbarStartNode && !toolbar ? null : (
    <ControlBand className={cn("overflow-x-auto overflow-y-hidden", formIsDirty ? "bg-brand-soft" : undefined)}>
      <div className="flex min-w-max shrink-0 items-center gap-2">
        {toolbarStartNode}
        {isCreate || formIsDirty ? (
          <div className="flex items-center gap-2">
            {formIsDirty ? (
              <Button
                type="button"
                variant="ghost"
                size="sm"
                disabled={pending}
                onClick={discardChanges}
              >
                {t("form.discard")}
              </Button>
            ) : null}
            <Button
              type="button"
              variant="primary"
              size="sm"
              loading={pending}
              disabled={formReadOnly || previewBlocked}
              onClick={() => {
                void submitForm();
              }}
            >
              {submitLabel ?? (isCreate ? t("form.create") : t("form.save"))}
            </Button>
          </div>
        ) : null}
        {!awaitingRecord && !readOnly && (
          declaredActions.length > 0 ||
          visibleDeleteAction !== undefined ||
          menuRecordActions.length > 0
        ) ? (
          <RecordActionBar
            record={displayRecord ?? null}
            actions={declaredActions}
            applyPatch={applyPatch}
            reload={reload}
            deleteAction={visibleDeleteAction}
            contributedActions={
              recordChromeContext && menuRecordActions.length > 0 ? (
                <RecordChromeProvider value={recordChromeContext}>
                  <SlotOutlet entries={menuRecordActions} />
                </RecordChromeProvider>
              ) : undefined
            }
            blocked={actionsBlocked}
          />
        ) : null}
        {!awaitingRecord && !readOnly && recordChromeContext ? (
          <RecordChromeProvider value={recordChromeContext}>
            <SlotOutlet entries={primaryRecordActions} />
          </RecordChromeProvider>
        ) : null}
      </div>
      <div className="min-w-2 flex-1" />
      <div className="flex min-w-max shrink-0 items-center gap-2">
        {recordChromeContext && !props.hideRecordChrome ? (
          <RecordChrome value={recordChromeContext} />
        ) : null}
        {toolbar}
      </div>
    </ControlBand>
  );

  const formElement = (
    <form
      className={cn("min-h-full bg-sheet", className)}
      onKeyDown={handleFormKeyDown}
      onSubmit={(event) => {
        void submitForm(event);
      }}
    >
      {controlBand}
      <div
        className={cn(
          FORM_VIEW_COLUMN_CLASS,
          "flex flex-col gap-6 pt-6",
          tabbed && activeRecordTab !== FORM_VIEW_OVERVIEW_TAB_ID
            ? "pb-4"
            : "pb-12",
        )}
      >
        {recordHeader()}
        {saveErrorBanner}
        {tabbed ? (
          <>
            <Tabs.List>
              {orderedTabs.map((tab) => (
                <Tabs.Tab
                  key={tab.id}
                  value={tab.id}
                  icon={"icon" in tab ? renderGlyph(tab.icon) : undefined}
                >
                  <SectionHeading as="span" label={resolveTabLabel(tab.label, i18n)}
                    count={"badge" in tab && tab.badge != null ? <Tabs.Count>{tab.badge}</Tabs.Count> : undefined} />
                </Tabs.Tab>
              ))}
            </Tabs.List>
            <Tabs.Panel
              value={FORM_VIEW_OVERVIEW_TAB_ID}
              keepMounted
              className="grid gap-6 pt-0"
            >
              {overviewWithFormExtras}
            </Tabs.Panel>
          </>
        ) : (
          overviewWithFormExtras
        )}
      </div>
    </form>
  );

  if (recordPresentation === "workspace" && !tabbed) {
    return (
      <div className={cn("flex h-full min-h-0 flex-col bg-sheet", className)}>
        <form
          className="flex min-h-0 flex-1 flex-col"
          onKeyDown={handleFormKeyDown}
          onSubmit={(event) => {
            void submitForm(event);
          }}
        >
          {controlBand}
          <div className="flex-none border-b border-border-subtle px-4 py-3">
            {recordHeader(true)}
            {saveErrorBanner}
          </div>
          <div className="min-h-0 flex-1 overflow-auto">
            <div className={cn(FORM_VIEW_COLUMN_CLASS, "grid gap-6 py-6")}>
              {overviewWithFormExtras}
            </div>
          </div>
        </form>
        {recordExtrasPanel}
      </div>
    );
  }

  if (recordPresentation === "workspace" && tabbed) {
    return (
      <Tabs
        value={activeRecordTab}
        onValueChange={setActiveRecordTab}
        variant="card"
        className={cn("flex h-full min-h-0 flex-col bg-sheet", className)}
      >
        <form
          className="contents"
          onKeyDown={handleFormKeyDown}
          onSubmit={(event) => {
            void submitForm(event);
          }}
        >
          {controlBand}
          <div className="flex-none border-b border-border-subtle px-4 pt-3">
            {recordHeader(true)}
            {saveErrorBanner}
            <Tabs.List className="mt-2">
              {orderedTabs.map((tab) => (
                <Tabs.Tab
                  key={tab.id}
                  value={tab.id}
                  icon={"icon" in tab ? renderGlyph(tab.icon) : undefined}
                >
                  <SectionHeading as="span" label={resolveTabLabel(tab.label, i18n)}
                    count={"badge" in tab && tab.badge != null ? <Tabs.Count>{tab.badge}</Tabs.Count> : undefined} />
                </Tabs.Tab>
              ))}
            </Tabs.List>
          </div>
          <Tabs.Panel
            value={FORM_VIEW_OVERVIEW_TAB_ID}
            className="min-h-0 flex-1 overflow-auto pt-0"
          >
            <div className={cn(FORM_VIEW_COLUMN_CLASS, "grid gap-6 py-6")}>{overviewWithFormExtras}</div>
          </Tabs.Panel>
        </form>
        {recordTabList.map((tab) => (
          <Tabs.Panel
            key={tab.id}
            value={tab.id}
            keepMounted={tab.keepMounted}
            className="min-h-0 flex-1 overflow-auto pt-0"
          >
            <ControlBandProvider host={undefined}>
              {renderRecordPanel(tab)}
            </ControlBandProvider>
          </Tabs.Panel>
        ))}
        {activeRecordTab === FORM_VIEW_OVERVIEW_TAB_ID
          ? recordExtrasPanel
          : null}
      </Tabs>
    );
  }

  if (!tabbed) {
    return (
      <>
        {formElement}
        {recordExtrasPanel}
      </>
    );
  }

  return (
    <Tabs value={activeRecordTab} onValueChange={setActiveRecordTab} variant="card">
      {formElement}
      {recordTabList.map((tab) => (
        <Tabs.Panel
          key={tab.id}
          value={tab.id}
          keepMounted={tab.keepMounted}
          className={cn(FORM_VIEW_COLUMN_CLASS, "pb-12")}
        >
          <ControlBandProvider host={undefined}>
            {renderRecordPanel(tab)}
          </ControlBandProvider>
        </Tabs.Panel>
      ))}
      {recordExtrasPanel}
    </Tabs>
  );
}
