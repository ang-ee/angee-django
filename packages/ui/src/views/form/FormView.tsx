import * as React from "react";
import { useModelMetadata } from "@angee/metadata";

import { Button } from "../../ui/button";
import { Tabs } from "../../ui/tabs";
import { useBreadcrumbLeafLabel } from "../../chrome/Breadcrumb";
import { renderGlyph } from "../../chrome/Glyph";
import { ControlBand, ControlBandProvider } from "../../layouts/ControlBand";
import { cn } from "../../lib/cn";
import { ContainerOutlet } from "../../lib/container-outlet";
import { ErrorBanner } from "../../fragments/ErrorBanner";
import { EmptyState } from "../../fragments/EmptyState";
import { Skeleton, SkeletonStatus } from "../../ui/skeleton";
import { errorMessage } from "../../feedback";
import {
  RecordChrome,
  RecordChromeProvider,
} from "../resource/record-chrome-context";
import { RecordActionBar } from "./RecordActionBar";
import { SaveDiscardActions, dirtyControlBandClassName } from "./SaveDiscardActions";
import { ActionFormProvider } from "./ActionFormProvider";
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
import { usePublishActiveRecordForm } from "./record-field-marks";

export { SectionHeading, type SectionHeadingProps } from "./SectionHeading";
export { RecordRailGroup, type RecordRailField, type RecordRailGroupProps } from "./form-view-rail";

export {
  acknowledgeFormSubmit,
} from "./form-view-surface";

export { FORM_CONTAINERS } from "./form-containers";

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
  /** Place the first two unlabeled groups side by side within one form overview. */
  groupLayout?: "stacked" | "paired";
  /** Editable line fields shown before the user expands advanced line details. */
  linePrimaryFields?: readonly string[];
  /** Read-only domain projections rendered beside editable line fields. */
  lineSupplementalColumns?: readonly EditableLineSupplementalColumn[];
  /** Domain-owned filters applied to relation pickers on editable lines. */
  lineRelationFilters?: EditableLinesProps["relationFilters"];
  /** Record chrome density and height behavior. */
  recordPresentation?: RecordPresentation;
  /** Overview visibility on forms without body tabs. */
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
});

function FormViewInstance(props: FormViewProps): React.ReactElement {
  const surface = useFormViewSurface({ ...props, overviewHidden: props.overviewTab?.hidden });
  return <ActionFormProvider {...surface.form}><FormViewContent {...props} surface={surface} /></ActionFormProvider>;
}

function FormViewContent({ surface, ...props }: FormViewProps & {
  surface: ReturnType<typeof useFormViewSurface>;
}): React.ReactElement {
  const { i18n } = useAppRuntime();
  const preview = useRuntimeViewAs();
  const previewBlocked = Boolean(preview.viewAs || preview.pending);
  const {
    readOnly = false,
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
    linePrimaryFields,
    lineSupplementalColumns,
    lineRelationFilters,
    recordPresentation = "document",
    overviewTab,
    publishBreadcrumbLabel = false,
    hideRecordChrome = false,
    className,
  } = props;
  const {
    t,
    activeRecordTab,
    setActiveRecordTab,
    isCreate,
    formReadOnly,
    formIsDirty,
    displayRecord,
    loadError,
    saveError,
    saveConflict,
    declaredActions,
    actionsBlocked,
    recordChromeContext: surfaceChromeContext,
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
  const { primary: primaryRecordActions, menu: menuRecordActions } = recordActions;
  const formModel = surfaceChromeContext?.canonicalResource;
  const formId = surfaceChromeContext?.recordId;
  const focusField = recordPanelContext?.focusField;
  const activeForm = React.useMemo(() => formModel && formId && focusField && !hideRecordChrome ? {
    model: formModel, id: formId, focusField,
  } : null, [formModel, formId, focusField, hideRecordChrome]);
  usePublishActiveRecordForm(activeForm);
  const [toolbarHost, setToolbarHost] = React.useState<HTMLElement | null>(null);
  const recordChromeContext = React.useMemo(
    () => surfaceChromeContext && { ...surfaceChromeContext, toolbarHost },
    [surfaceChromeContext, toolbarHost],
  );
  const availableDeclaredActions = readOnly
    ? declaredActions.filter((action) => action.run || action.submit)
    : declaredActions;
  useBreadcrumbLeafLabel(
    titleText(
      recordRepresentationValue(displayRecord, surface.modelMetadata),
      "",
    ) || null,
    publishBreadcrumbLabel && !isCreate,
  );
  const awaitingRecord = !isCreate && displayRecord == null && loading;
  if (!isCreate && !awaitingRecord && displayRecord == null) {
    if (loadError) return <ErrorBanner title={t("form.loadFailed")}
      description={errorMessage(loadError, t("form.loadFailed"))}
      actions={<Button type="button" size="sm" onClick={() => void reload()}>{t("collection.retry")}</Button>} />;
    return <EmptyState icon="lock" title={t("form.recordUnavailable")} className="min-h-64 p-8" />;
  }
  const toolbarStartNode =
    typeof toolbarStart === "function"
      ? toolbarStart(recordToolbarContext)
      : toolbarStart;
  const hasBodyTabs = surface.bodyTabSections.length > 0;
  const overviewActive = !tabbed || (hasBodyTabs
    ? surface.bodyTabSections.some((section) => section.key === activeRecordTab)
    : activeRecordTab === FORM_VIEW_OVERVIEW_TAB_ID);
  const orderedTabs = [
    ...(hasBodyTabs ? surface.bodyTabSections.map((section) => ({ ...section, id: section.key }))
      : overviewTab?.hidden && recordTabList.length > 0 ? []
      : [{ id: FORM_VIEW_OVERVIEW_TAB_ID, label: t("form.tabOverview") }]),
    ...recordTabList,
  ];
  const tabStrip = <Tabs.List>
    {orderedTabs.map((tab) => <Tabs.Tab key={tab.id} value={tab.id}
      icon={"icon" in tab ? renderGlyph(tab.icon) : undefined}>
      <SectionHeading as="span" label={resolveTabLabel(tab.label, i18n)}
        count={"badge" in tab && tab.badge != null ? <Tabs.Count>{tab.badge}</Tabs.Count> : undefined} />
    </Tabs.Tab>)}
  </Tabs.List>;
  const overview = awaitingRecord ? (
    <SkeletonStatus label={t("form.loading")} className="grid gap-4 py-5">
      <Skeleton shape="text" className="h-6 w-2/3" />
      <Skeleton className="h-32 w-full" />
    </SkeletonStatus>
  ) : (
    <FormViewOverview
      surface={surface} layout={layout} groupLayout={groupLayout} tabStrip={tabStrip}
      linePrimaryFields={linePrimaryFields}
      lineSupplementalColumns={lineSupplementalColumns}
      lineRelationFilters={lineRelationFilters}
    />
  );
  const recordExtrasPanel =
    !awaitingRecord && recordPanelContext && recordExtras ? (
      <div className={cn(FORM_VIEW_COLUMN_CLASS, "pb-12")}>
        {recordExtras(recordPanelContext)}
      </div>
    ) : null;
  const withRail = (body: React.ReactNode, active: boolean, workspace = false) => {
    const hasRail = active && surface.railGroups.length > 0;
    return <div className={cn("@container w-full", workspace && "h-full min-h-0")}>
      <div className={cn("grid gap-6", workspace && "h-full min-h-0", workspace && (hasRail
        ? "grid-rows-[minmax(0,1fr)_auto] @min-[52rem]:grid-rows-none"
        : "grid-rows-[minmax(0,1fr)]"), hasRail && "@min-[52rem]:grid-cols-[minmax(0,1fr)_14rem]")}>
        <div className={cn("min-w-0", workspace && "flex min-h-0 flex-col")}>{body}</div>
        {active ? <FormViewRail surface={surface} /> : null}
      </div>
    </div>;
  };
  const overviewContent = withRail(<>
    {overview}
    {!awaitingRecord && formExtras ? <div className="pt-2">{formExtras(recordToolbarContext)}</div> : null}
  </>, overviewActive);
  const overviewWithFormExtras = recordChromeContext
    ? <RecordChromeProvider value={recordChromeContext}>{overviewContent}</RecordChromeProvider>
    : overviewContent;
  const renderRecordPanel = (tab: RecordTabDescriptor) => {
    if (!recordPanelContext || awaitingRecord) return null;
    const active = activeRecordTab === tab.id;
    const content = withRail(tab.render({ ...recordPanelContext, active }), active, recordPresentation === "workspace" || tab.presentation === "full-bleed");
    return recordChromeContext
      ? <RecordChromeProvider value={recordChromeContext}>{content}</RecordChromeProvider>
      : content;
  };
  const formTitle = awaitingRecord ? t("form.loading") : typeof title === "function" ? title(recordToolbarContext) : title;
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
  const rawControlBand = readOnly && props.hideRecordChrome && !toolbarStartNode && !toolbar ? null : (
    <ControlBand className={cn("overflow-x-auto overflow-y-hidden", formIsDirty ? dirtyControlBandClassName : undefined)}>
      <div className="flex min-w-max shrink-0 items-center gap-2">
        {toolbarStartNode}
        <SaveDiscardActions
          isDirty={formIsDirty}
          alwaysShowSave={isCreate}
          saveIntent={isCreate ? "create" : "save"}
          saveLabel={submitLabel}
          pending={pending}
          saveDisabled={formReadOnly || previewBlocked}
          onDiscard={discardChanges}
          onSave={() => { void submitForm(); }}
        />
        <span ref={setToolbarHost} className="contents" />
        {!awaitingRecord && (
          availableDeclaredActions.length > 0 ||
          visibleDeleteAction !== undefined ||
          (!readOnly && menuRecordActions.length > 0)
        ) ? (
          <RecordActionBar
            record={displayRecord ?? null}
            actions={availableDeclaredActions}
            applyPatch={applyPatch}
            reload={reload}
            deleteAction={visibleDeleteAction}
            contributedActions={
              !readOnly && recordChromeContext && menuRecordActions.length > 0 ? (
                <ContainerOutlet entries={menuRecordActions} />
              ) : undefined
            }
            blocked={actionsBlocked}
          />
        ) : null}
        {!awaitingRecord && !readOnly && recordChromeContext ? (
          <ContainerOutlet entries={primaryRecordActions} />
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
  const controlBand = recordChromeContext
    ? <RecordChromeProvider value={recordChromeContext}>{rawControlBand}</RecordChromeProvider> : rawControlBand;

  const errorBanners = <>
    {loadError ? <ErrorBanner title={t("form.loadFailed")}
      description={errorMessage(loadError, t("form.loadFailed"))}
      actions={<Button type="button" size="sm" disabled={loading} onClick={() => void reload()}>{t("collection.retry")}</Button>} /> : null}
    <ErrorBanner
      description={saveError}
      title={t(saveConflict ? "form.saveConflict" : "form.saveFailed")}
      actions={saveConflict ? (
        <Button type="button" variant="secondary" size="sm" onClick={() => { discardChanges(); reload(); }}>
          {t("form.reloadSaved")}
        </Button>
      ) : undefined}
    />
  </>;

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
        {errorBanners}
        {tabbed && !hasBodyTabs ? (
          <>
            {tabStrip}
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
            {errorBanners}
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

  // Keep one panel tree across presentations so retained editor drafts survive tab changes.
  if (tabbed && (recordPresentation === "workspace" || recordTabList.some((tab) => tab.presentation === "full-bleed"))) {
    const compactHeader = recordPresentation === "workspace";
    const workspace = recordPresentation === "workspace"
      || recordTabList.some((tab) => tab.id === activeRecordTab && tab.presentation === "full-bleed");
    return (
      <Tabs
        value={activeRecordTab}
        onValueChange={setActiveRecordTab}
        variant="card"
        className={cn("bg-sheet", workspace && "flex h-full min-h-0 flex-col", className)}
      >
        <form
          className={workspace ? "contents" : "min-h-full"}
          onKeyDown={handleFormKeyDown}
          onSubmit={(event) => {
            void submitForm(event);
          }}
        >
          {controlBand}
          <div className={compactHeader ? "flex-none border-b border-border-subtle px-4 pt-3" : cn(FORM_VIEW_COLUMN_CLASS, "flex-none flex flex-col gap-6 pt-6", activeRecordTab === FORM_VIEW_OVERVIEW_TAB_ID ? "pb-6" : "pb-4")}>
            {recordHeader(compactHeader)}
            {errorBanners}
            {hasBodyTabs ? overviewWithFormExtras : <div className={compactHeader ? "mt-2" : undefined}>{tabStrip}</div>}
          </div>
          {!hasBodyTabs ? <Tabs.Panel
            value={FORM_VIEW_OVERVIEW_TAB_ID}
            keepMounted={recordPresentation !== "workspace"}
            className="min-h-0 flex-1 overflow-auto pt-0"
          >
            <div className={cn(FORM_VIEW_COLUMN_CLASS, "grid gap-6", workspace ? "py-6" : "pb-12")}>{overviewWithFormExtras}</div>
          </Tabs.Panel> : null}
        </form>
        {recordTabList.map((tab) => (
          <Tabs.Panel
            key={tab.id}
            value={tab.id}
            keepMounted={tab.keepMounted}
            className={recordPresentation === "workspace" || tab.presentation === "full-bleed"
              ? cn("min-h-0 flex-1 pt-0", tab.presentation === "full-bleed" ? "overflow-hidden" : "overflow-auto")
              : cn(FORM_VIEW_COLUMN_CLASS, "pb-12")}
          >
            <ControlBandProvider host={undefined}>
              {renderRecordPanel(tab)}
            </ControlBandProvider>
          </Tabs.Panel>
        ))}
        {overviewActive
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
