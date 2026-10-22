import * as React from "react";

import { Glyph } from "../chrome/Glyph";
import { EmptyState } from "../fragments/EmptyState";
import { MetricStrip, type MetricTileValue } from "../fragments/MetricStrip";
import { RecordHeader, type RecordHeaderStatus } from "../fragments/RecordHeader";
import { useUiT } from "../i18n";
import { Button } from "../ui/button";
import { ActionFormDialog } from "../views/form/ActionFormDialog";
import { RecordActionBar } from "../views/form/RecordActionBar";
import type { ActionDescriptor, ActionFormContext } from "../views/page/Action";
import { Page } from "./Page";
import { PageBody } from "./PageBody";
import { PageToolbar } from "./PageToolbar";
import { SectionNav, type SectionNavItem } from "./SectionNav";

/** A report runs on no record: its verbs and parameter form act on the page. */
const PAGE_CONTEXT: ActionFormContext = { record: null, selectedIds: [] };

export interface ReportPageProps {
  title: React.ReactNode;
  /** The kind and container caption above the title ("Accounting · Reports"). */
  crumbs?: React.ReactNode;
  /** The scope the report was run for (company, currency, period), one node each. */
  facts?: readonly React.ReactNode[];
  /** How to read the report; prose under the title. */
  description?: React.ReactNode;
  /** The run's own state as a status pill (balanced, incomplete). */
  status?: RecordHeaderStatus;
  /** Something to read before the rows: an `Alert` for an unbalanced ledger. */
  notice?: React.ReactNode;
  /** Presentations of the same run; the active one carries `active`. */
  views?: readonly SectionNavItem[];
  /**
   * The verb that collects the run's parameters (`args` + `submit`). Its dialog
   * opens by itself while the page is not `configured`, and from the empty state.
   */
  parameters?: ActionDescriptor;
  /** Whether the URL carries a complete parameter set; `false` shows `empty` instead of the rows. */
  configured?: boolean;
  /** Page verbs (refresh, export, post): record-less descriptors on the toolbar. */
  actions?: readonly ActionDescriptor[];
  /** The run's headline totals. */
  metrics?: readonly MetricTileValue[];
  /** Copy for the unconfigured state. */
  empty?: { title: React.ReactNode; description?: React.ReactNode };
  /** The rows: `RowsListView`s and their drill-ins. */
  children?: React.ReactNode;
}

/**
 * A report page: a parameterized, read-only run presented as a subject. The
 * header carries the report's title, scope facts and run status; the toolbar its
 * views (`SectionNav`) and verbs (`RecordActionBar` on no record, the parameter
 * form as `ActionFormDialog`); the body its metrics and rows. Pages declare the
 * run; nothing here is report-specific.
 * @kind page
 */
export function ReportPage({
  title,
  crumbs,
  facts,
  description,
  status,
  notice,
  views,
  parameters,
  configured = true,
  actions,
  metrics,
  empty,
  children,
}: ReportPageProps): React.ReactElement {
  const t = useUiT();
  // The parameters dialog opens on its own only while the run has nothing to show.
  const [open, setOpen] = React.useState(!configured && parameters != null);
  const toolbarActions = React.useMemo(
    () => (actions ?? []).map((action) => ({ placement: "toolbar" as const, ...action })),
    [actions],
  );
  const parametersButton = parameters ? (
    <Button type="button" size="sm" variant="secondary" onClick={() => setOpen(true)}>
      <Glyph name={parameters.icon ?? "sliders-horizontal"} />
      {parameters.label}
    </Button>
  ) : null;
  const hasToolbar = Boolean(views?.length) || parametersButton !== null || toolbarActions.length > 0;
  return (
    <Page height="auto" overflow="visible">
      <RecordHeader
        headingLevel={1}
        title={title}
        crumbs={crumbs}
        description={description}
        status={status}
        meta={facts?.length ? facts.map((fact, index) => <span key={index}>{fact}</span>) : undefined}
      />
      {hasToolbar ? (
        <PageToolbar
          start={views?.length ? <SectionNav items={views} label={t("reportPage.views")} /> : undefined}
          end={<>
            {parametersButton}
            {toolbarActions.length > 0 ? <RecordActionBar record={null} actions={toolbarActions} /> : null}
          </>}
        />
      ) : null}
      <PageBody gutter="compact" className="flex flex-col gap-4">
        {notice}
        {configured && metrics?.length ? <MetricStrip metrics={metrics} /> : null}
        {configured ? children : (
          <EmptyState
            fill
            icon="chart-no-axes-combined"
            title={empty?.title ?? t("reportPage.unconfigured")}
            description={empty?.description}
            actions={parameters ? (
              <Button type="button" size="sm" variant="primary" onClick={() => setOpen(true)}>
                {parameters.label}
              </Button>
            ) : undefined}
          />
        )}
      </PageBody>
      {parameters && open ? (
        // The dialog's verb runs the report; the trigger's label names the parameters.
        <ActionFormDialog action={{ submitLabel: t("reportPage.run"), ...parameters }} context={PAGE_CONTEXT} open onOpenChange={setOpen} />
      ) : null}
    </Page>
  );
}
