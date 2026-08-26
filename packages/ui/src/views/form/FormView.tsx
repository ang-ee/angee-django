import * as React from "react";

import { Button } from "../../ui/button";
import { Tabs } from "../../ui/tabs";
import { renderGlyph } from "../../chrome/Glyph";
import { ControlBand, ControlBandProvider } from "../../layouts/ControlBand";
import { cn } from "../../lib/cn";
import { SlotOutlet } from "../../lib/slot-outlet";
import { ErrorBanner } from "../../fragments/ErrorBanner";
import {
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
  type RecordToolbarContext,
  type UseFormViewSurfaceProps,
} from "./form-view-surface";
import {
  FORM_VIEW_COLUMN_CLASS,
  FormViewOverview,
  FormViewRecordHeader,
} from "./form-view-body";

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
  FormSubmitContext,
  RecordPanelContext,
  RecordTabDescriptor,
  RecordToolbarContext,
} from "./form-view-surface";

export interface FormViewProps extends UseFormViewSurfaceProps {
  submitLabel?: React.ReactNode;
  toolbarStart?:
    | React.ReactNode
    | ((context: RecordToolbarContext) => React.ReactNode);
  toolbar?: React.ReactNode;
  /** Saved-record content rendered below, but outside, the form element. */
  recordExtras?: (context: RecordPanelContext) => React.ReactNode;
  /** Group presentation; ungrouped/title/body/status placement is unchanged. */
  layout?: "stacked" | "tabs";
  className?: string;
}

/**
 * Thin form shell. `useFormViewSurface` owns declarations through submit/diff;
 * the section owners below only bind that view model to shared UI primitives.
 */
export function FormView(props: FormViewProps): React.ReactElement {
  const {
    resource,
    id,
    fields,
    groups,
    children,
    actions,
    returning,
    defaultValues,
    onSaved,
    submit,
    createSubmit,
    recordTabs,
    deleteAction,
    deleteVisibleWhen,
    submitLabel,
    toolbarStart,
    toolbar,
    recordExtras,
    layout = "stacked",
    className,
  } = props;
  const surface = useFormViewSurface({
    resource,
    id,
    fields,
    groups,
    children,
    actions,
    returning,
    defaultValues,
    onSaved,
    submit,
    createSubmit,
    recordTabs,
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
    recordChrome,
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
  const overviewBody = <FormViewOverview surface={surface} layout={layout} />;
  const recordExtrasPanel =
    recordPanelContext && recordExtras ? (
      <div className={cn(FORM_VIEW_COLUMN_CLASS, "pb-12")}>
        {recordExtras(recordPanelContext)}
      </div>
    ) : null;

  const formElement = (
    <form
      className={cn("min-h-full bg-sheet", className)}
      onSubmit={(event) => {
        void submitForm(event);
      }}
    >
      <ControlBand className={formIsDirty ? "bg-brand-soft" : undefined}>
        <div className="flex min-w-0 items-center gap-2">
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
          {declaredActions.length > 0 || visibleDeleteAction !== undefined ? (
            <RecordActionBar
              record={displayRecord ?? null}
              actions={declaredActions}
              applyPatch={applyPatch}
              reload={reload}
              deleteAction={visibleDeleteAction}
            />
          ) : null}
          {recordChromeContext ? (
            <RecordChromeProvider value={recordChromeContext}>
              <SlotOutlet entries={recordActions} />
            </RecordChromeProvider>
          ) : null}
        </div>
        <div className="min-w-2 flex-1" />
        <div className="flex min-w-0 items-center gap-2">
          {recordChromeContext ? (
            <RecordChromeProvider value={recordChromeContext}>
              <SlotOutlet entries={recordChrome} />
            </RecordChromeProvider>
          ) : null}
          {toolbar}
        </div>
      </ControlBand>
      <div
        className={cn(
          FORM_VIEW_COLUMN_CLASS,
          "flex flex-col gap-6 pt-6",
          tabbed && activeRecordTab !== FORM_VIEW_OVERVIEW_TAB_ID
            ? "pb-4"
            : "pb-12",
        )}
      >
        <FormViewRecordHeader surface={surface} />
        <ErrorBanner description={saveError} title={t("form.saveFailed")} />
        {tabbed ? (
          <>
            <Tabs.List>
              <Tabs.Tab value={FORM_VIEW_OVERVIEW_TAB_ID}>
                {t("form.tabOverview")}
              </Tabs.Tab>
              {recordTabList.map((tab) => (
                <Tabs.Tab key={tab.id} value={tab.id} icon={renderGlyph(tab.icon)}>
                  {tab.label}
                </Tabs.Tab>
              ))}
            </Tabs.List>
            <Tabs.Panel
              value={FORM_VIEW_OVERVIEW_TAB_ID}
              keepMounted
              className="grid gap-6 pt-0"
            >
              {overviewBody}
            </Tabs.Panel>
          </>
        ) : (
          overviewBody
        )}
      </div>
    </form>
  );

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
            {recordPanelContext ? tab.render(recordPanelContext) : null}
          </ControlBandProvider>
        </Tabs.Panel>
      ))}
      {recordExtrasPanel}
    </Tabs>
  );
}
