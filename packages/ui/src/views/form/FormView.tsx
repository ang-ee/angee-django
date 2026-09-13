import * as React from "react";
import { useModelMetadata } from "@angee/metadata";

import { Button } from "../../ui/button";
import { Tabs } from "../../ui/tabs";
import { renderGlyph } from "../../chrome/Glyph";
import { ControlBand, ControlBandProvider } from "../../layouts/ControlBand";
import { cn } from "../../lib/cn";
import { SlotOutlet } from "../../lib/slot-outlet";
import { ErrorBanner } from "../../fragments/ErrorBanner";
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
  type OverviewTabOptions,
  type RecordPresentation,
  type RecordToolbarContext,
  type UseFormViewSurfaceProps,
} from "./form-view-surface";
import {
  FORM_VIEW_COLUMN_CLASS,
  FormViewOverview,
  FormViewRecordHeader,
} from "./form-view-body";

export {
  acknowledgeFormSubmit,
} from "./form-view-surface";

export {
  FORM_VIEW_RECORD_ACTIONS_SLOT,
  FORM_VIEW_RECORD_CHROME_SLOT,
  FORM_VIEW_SECTIONS_SLOT,
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
  /** Group presentation; ungrouped/title/body/status placement is unchanged. */
  layout?: "stacked" | "tabs";
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
export function FormView(props: FormViewProps): React.ReactElement {
  const model = useModelMetadata(props.resource);
  const identity = `${model?.resource?.schemaName ?? "default"}:${model?.resource?.modelLabel ?? props.resource}:${props.id ?? "create"}`;
  return <FormViewInstance key={identity} {...props} />;
}

function FormViewInstance(props: FormViewProps): React.ReactElement {
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
    layout = "stacked",
    recordPresentation = "document",
    defaultRecordTab,
    overviewTab,
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
    declaredActions,
    recordChromeContext,
    recordActions,
    recordPanelContext,
    recordToolbarContext,
    recordTabList,
    tabbed,
    visibleDeleteAction,
    pending,
    submitForm,
    discardChanges,
    applyPatch,
    reload,
  } = surface;
  const toolbarStartNode =
    typeof toolbarStart === "function"
      ? toolbarStart(recordToolbarContext)
      : toolbarStart;
  const overview = <FormViewOverview surface={surface} layout={layout} />;
  const overviewLabel = overviewTab?.label ?? t("form.tabOverview");
  const orderedTabs = overviewTab?.position === "last"
    ? [...recordTabList, { id: FORM_VIEW_OVERVIEW_TAB_ID, label: overviewLabel }]
    : [{ id: FORM_VIEW_OVERVIEW_TAB_ID, label: overviewLabel }, ...recordTabList];
  const overviewBody = recordChromeContext ? (
    <RecordChromeProvider value={recordChromeContext}>
      {overview}
    </RecordChromeProvider>
  ) : overview;
  const recordExtrasPanel =
    recordPanelContext && recordExtras ? (
      <div className={cn(FORM_VIEW_COLUMN_CLASS, "pb-12")}>
        {recordExtras(recordPanelContext)}
      </div>
    ) : null;
  const overviewWithFormExtras = (
    <>
      {overviewBody}
      {formExtras ? <div className="pt-2">{formExtras(recordToolbarContext)}</div> : null}
    </>
  );
  const formTitle = typeof title === "function" ? title(recordToolbarContext) : title;

  const handleFormKeyDown = (event: React.KeyboardEvent<HTMLFormElement>) => {
    if (
      !isCreate ||
      event.key !== "Enter" ||
      event.defaultPrevented ||
      event.nativeEvent.isComposing ||
      !(event.target instanceof HTMLInputElement) ||
      event.target.type !== "text"
    ) {
      return;
    }
    event.preventDefault();
    void submitForm();
  };
  const controlBand = (
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
              disabled={formReadOnly}
              onClick={() => {
                void submitForm();
              }}
            >
              {submitLabel ?? (isCreate ? t("form.create") : t("form.save"))}
            </Button>
          </div>
        ) : null}
        {!readOnly && (declaredActions.length > 0 || visibleDeleteAction !== undefined) ? (
          <RecordActionBar
            record={displayRecord ?? null}
            actions={declaredActions}
            applyPatch={applyPatch}
            reload={reload}
            deleteAction={visibleDeleteAction}
          />
        ) : null}
        {!readOnly && recordChromeContext ? (
          <RecordChromeProvider value={recordChromeContext}>
            <SlotOutlet entries={recordActions} />
          </RecordChromeProvider>
        ) : null}
      </div>
      <div className="min-w-2 flex-1" />
      <div className="flex min-w-max shrink-0 items-center gap-2">
        {recordChromeContext ? (
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
        <FormViewRecordHeader surface={surface} title={formTitle} />
        <ErrorBanner description={saveError} title={t("form.saveFailed")} />
        {tabbed ? (
          <>
            <Tabs.List>
              {orderedTabs.map((tab) => (
                <Tabs.Tab
                  key={tab.id}
                  value={tab.id}
                  icon={"icon" in tab ? renderGlyph(tab.icon) : undefined}
                >
                  {tab.label}
                  {"badge" in tab && tab.badge != null ? (
                    <Tabs.Count>{tab.badge}</Tabs.Count>
                  ) : null}
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
            <FormViewRecordHeader surface={surface} compact title={formTitle} />
            <ErrorBanner description={saveError} title={t("form.saveFailed")} />
            <Tabs.List className="mt-2">
              {orderedTabs.map((tab) => (
                <Tabs.Tab
                  key={tab.id}
                  value={tab.id}
                  icon={"icon" in tab ? renderGlyph(tab.icon) : undefined}
                >
                  {tab.label}
                  {"badge" in tab && tab.badge != null ? <Tabs.Count>{tab.badge}</Tabs.Count> : null}
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
            className="min-h-0 flex-1 overflow-hidden pt-0"
          >
            <ControlBandProvider host={undefined}>
              {recordPanelContext ? (recordChromeContext ? <RecordChromeProvider value={recordChromeContext}>{tab.render(recordPanelContext)}</RecordChromeProvider> : tab.render(recordPanelContext)) : null}
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
            {recordPanelContext ? (
              recordChromeContext ? (
                <RecordChromeProvider value={recordChromeContext}>
                  {tab.render(recordPanelContext)}
                </RecordChromeProvider>
              ) : (
                tab.render(recordPanelContext)
              )
            ) : null}
          </ControlBandProvider>
        </Tabs.Panel>
      ))}
      {recordExtrasPanel}
    </Tabs>
  );
}
