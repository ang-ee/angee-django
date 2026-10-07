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
  useFormViewSurface,
  type FormViewPane,
  type RecordPanelContext,
  type RecordPresentation,
  type RecordToolbarContext,
  type UseFormViewSurfaceProps,
} from "./form-view-surface";
import {
  FORM_VIEW_COLUMN_CLASS,
  FormViewLinesPane,
  FormViewPaneGroup,
  FormViewRail,
  FormViewRecordHeader,
  FormViewSheet,
} from "./form-view-body";
import { EDITABLE_LINES_SECTION } from "./form-view-model";
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
  /** Non-form content rendered after the sheet's sections for both create and edit. */
  formExtras?: (context: RecordToolbarContext) => React.ReactNode;
  /** Compact read-only content rendered with the record heading. */
  headerExtras?: (context: RecordToolbarContext) => React.ReactNode;
  /** One concise domain context line under the record title. */
  contextLine?: (context: RecordToolbarContext) => React.ReactNode;
  /** Place the first two unlabeled groups side by side within the sheet. */
  groupLayout?: "stacked" | "paired";
  /** Editable line fields shown before the user expands advanced line details. */
  linePrimaryFields?: readonly string[];
  /** Authored header, help text, choices or widget for named editable line fields. */
  lineFields?: EditableLinesProps["fields"];
  /** Read-only domain projections rendered beside editable line fields. */
  lineSupplementalColumns?: readonly EditableLineSupplementalColumn[];
  /** Domain-owned filters applied to relation pickers on editable lines. */
  lineRelationFilters?: EditableLinesProps["relationFilters"];
  /** Content under the editable lines (totals and the like); nothing else shows it. */
  lineFooter?: (context: RecordToolbarContext) => React.ReactNode;
  /** Record chrome density and height behavior. */
  recordPresentation?: RecordPresentation;
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
  const surface = useFormViewSurface(props);
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
    groupLayout = "stacked",
    linePrimaryFields,
    lineFields,
    lineSupplementalColumns,
    lineRelationFilters,
    lineFooter,
    recordPresentation = "document",
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
    panes,
    tabbed,
    recordTabPending,
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
  // The sheet is the record's own fields outside its pane groups, always shown; panes follow it.
  // One pane renders without a strip, beneath the sheet under its own heading.
  const lonePane = tabbed ? undefined : panes[0];
  const recordTabById = new Map(recordTabList.map((tab) => [tab.id, tab]));
  const fullBleed = (pane: FormViewPane) => recordTabById.get(pane.id)?.presentation === "full-bleed";
  const workspace = recordPresentation === "workspace";
  // A full-bleed pane fills the height under the sheet and the strip; the sheet then scrolls in
  // its own region. Every other record flows, a workspace record inside its own scroll area.
  const fillActive = panes.some((pane) => pane.id === activeRecordTab && fullBleed(pane));
  const lineProps = {
    linePrimaryFields,
    lineFields,
    lineSupplementalColumns,
    lineRelationFilters,
    lineFooter: lineFooter && !awaitingRecord ? () => lineFooter(recordToolbarContext) : undefined,
  };
  const sheetBody = awaitingRecord ? (
    <SkeletonStatus label={t("form.loading")} className="grid gap-4 py-5">
      <Skeleton shape="text" className="h-6 w-2/3" />
      <Skeleton className="h-32 w-full" />
    </SkeletonStatus>
  ) : <FormViewSheet surface={surface} groupLayout={groupLayout} {...lineProps} />;
  const hasRail = surface.railGroups.length > 0;
  const sheetContent = <div className="@container w-full">
    <div className={cn("grid gap-6", hasRail && "@min-[52rem]:grid-cols-[minmax(0,1fr)_14rem]")}>
      <div className="min-w-0">
        {sheetBody}
        {!awaitingRecord && formExtras ? <div className="pt-2">{formExtras(recordToolbarContext)}</div> : null}
      </div>
      <FormViewRail surface={surface} />
    </div>
  </div>;
  const sheet = recordChromeContext
    ? <RecordChromeProvider value={recordChromeContext}>{sheetContent}</RecordChromeProvider>
    : sheetContent;
  const recordExtrasPanel =
    !awaitingRecord && recordPanelContext && recordExtras && !fillActive ? (
      <div className={cn(FORM_VIEW_COLUMN_CLASS, "pb-12")}>
        {recordExtras(recordPanelContext)}
      </div>
    ) : null;
  const paneStrip = tabbed ? <Tabs.List>
    {panes.map((pane) => <Tabs.Tab key={pane.id} value={pane.id} icon={renderGlyph(pane.icon)}>
      <SectionHeading as="span" label={resolveTabLabel(pane.label, i18n)}
        count={pane.badge != null ? <Tabs.Count>{pane.badge}</Tabs.Count> : undefined} />
    </Tabs.Tab>)}
  </Tabs.List> : lonePane ? <SectionHeading label={resolveTabLabel(lonePane.label, i18n)} count={lonePane.badge}
    className="border-b border-border-subtle pb-1" /> : null;
  const renderPane = (pane: FormViewPane) => {
    if (awaitingRecord || recordTabPending) return null;
    const active = activeRecordTab === pane.id;
    if (pane.id === EDITABLE_LINES_SECTION) return <FormViewLinesPane surface={surface} {...lineProps} />;
    const paneSection = surface.paneSections.find((section) => section.pane === pane.id);
    if (paneSection) {
      // A pane group's content reads the record like the sheet's sections do.
      const group = <FormViewPaneGroup surface={surface} section={paneSection} />;
      return recordChromeContext ? <RecordChromeProvider value={recordChromeContext}>{group}</RecordChromeProvider> : group;
    }
    const tab = recordTabById.get(pane.id);
    // Only the active pane, or one asked to stay mounted, has content: the strip's own selection
    // can trail the record's for a render, and an inactive pane must not mount meanwhile.
    if (!tab || !recordPanelContext || (!active && !tab.keepMounted)) return null;
    const content = tab.render({ ...recordPanelContext, active });
    return recordChromeContext
      ? <RecordChromeProvider value={recordChromeContext}>{content}</RecordChromeProvider>
      : content;
  };
  // Tabbed, each pane is a tab panel; a lone one is the plain region beneath the sheet.
  const paneRegions = panes.map((pane) => {
    const paneClass = fullBleed(pane) ? "min-h-0 flex-1 overflow-hidden pt-0" : cn(FORM_VIEW_COLUMN_CLASS, "pt-3 pb-12");
    const content = <ControlBandProvider host={undefined}>{renderPane(pane)}</ControlBandProvider>;
    // The lines pane stays mounted so its draft rows and their errors survive switching panes.
    const keepMounted = pane.id === EDITABLE_LINES_SECTION || recordTabById.get(pane.id)?.keepMounted;
    return tabbed
      ? <Tabs.Panel key={pane.id} value={pane.id} keepMounted={keepMounted} className={paneClass}>{content}</Tabs.Panel>
      : <div key={pane.id} className={paneClass}>{content}</div>;
  });
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

  // One Tabs root, with or without a strip, is the record's sheet background around the form and
  // its panes: panes coming, going or switching never remount the form, and keep-mounted panes
  // keep their drafts.
  const rootClass = cn("bg-sheet",
    fillActive ? "flex h-full min-h-0 flex-col" : workspace ? "h-full min-h-0 overflow-auto" : "min-h-full",
    className);
  return <Tabs value={activeRecordTab ?? null} onValueChange={setActiveRecordTab} variant="card" className={rootClass}>
    <form
      className={fillActive ? "contents" : undefined}
      onKeyDown={handleFormKeyDown}
      onSubmit={(event) => {
        void submitForm(event);
      }}
    >
      {controlBand}
      {workspace ? (
        <div className="sticky top-0 z-10 flex-none border-b border-border-subtle bg-sheet px-4 py-3">
          {recordHeader(true)}
          {errorBanners}
        </div>
      ) : null}
      <div className={fillActive ? "max-h-[50%] min-h-0 flex-none overflow-auto" : undefined}>
        <div className={cn(FORM_VIEW_COLUMN_CLASS, "flex flex-col gap-6 pt-6", panes.length > 0 ? "pb-6" : "pb-12")}>
          {workspace ? null : <>{recordHeader()}{errorBanners}</>}
          {sheet}
        </div>
      </div>
    </form>
    {paneStrip ? <div className={cn(FORM_VIEW_COLUMN_CLASS, "flex-none")}>{paneStrip}</div> : null}
    {paneRegions}
    {recordExtrasPanel}
  </Tabs>;
}
