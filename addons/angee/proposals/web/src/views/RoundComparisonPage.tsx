import {
  EmptyState,
  ErrorBanner,
  FieldDescriptorControl,
  LoadingPanel,
  Page,
  PageBody,
  PageHeader,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
  TextLink,
  useRouteHref,
  type FieldDescriptor,
} from "@angee/ui";
import { useParams } from "@tanstack/react-router";
import * as React from "react";

import {
  type ComparisonAnswer,
  type ComparisonProposal,
  type ComparisonTopic,
  useRoundComparisonData,
} from "../comparison-data";
import {
  comparisonCellValue,
  comparisonRows,
  isEmptyComparisonValue,
  proposalColumnLabel,
  type ComparisonFactField,
  type ComparisonRow,
} from "../comparison-model";
import { useProposalsT, type ProposalsT } from "../i18n";
import { RoundCloseControls } from "../round-actions";

const FACT_DESCRIPTORS: Readonly<Record<ComparisonFactField, FieldDescriptor>> = {
  cost: { name: "cost", widget: "money", currencyField: "currency" },
  staffing: { name: "staffing", widget: "text" },
  timeframe_start: { name: "timeframe_start", widget: "date" },
  timeframe_end: { name: "timeframe_end", widget: "date" },
  confidence: { name: "confidence", widget: "statusBadge" },
  valid_until: { name: "valid_until", widget: "date" },
};

/** Headline proposal tabulation route; visibility is exactly the server result. */
export function RoundComparisonPage(): React.ReactElement {
  const { id = "" } = useParams({ strict: false }) as { id?: string };
  const t = useProposalsT();
  const routeHref = useRouteHref();
  const data = useRoundComparisonData(id);
  const roundName = String(data.round?.name ?? id);

  return (
    <Page>
      <PageHeader
        title={t("comparison.title", { round: roundName })}
        description={t("comparison.description")}
        crumbs={
          <TextLink href={routeHref("proposals.rounds.record", { id })}>
            {t("comparison.back")}
          </TextLink>
        }
        actions={
          data.round ? (
            <RoundCloseControls roundId={id} round={data.round} />
          ) : null
        }
      />
      <PageBody gutter="none">
        {data.error ? (
          <div className="p-5">
            <ErrorBanner
              title={t("comparison.error")}
              description={data.error.message}
            />
          </div>
        ) : data.fetching && !data.round ? (
          <LoadingPanel message={t("comparison.loading")} />
        ) : !data.fetching && data.proposals.length === 0 ? (
          <EmptyState
            fill
            icon="proposals-round"
            title={t("comparison.empty.title")}
            description={t("comparison.empty.description")}
          />
        ) : (
          <RoundComparisonGrid
            topics={data.topics}
            proposals={data.proposals}
            answers={data.answers}
            proposalHref={(proposalId) =>
              routeHref("proposals.proposals.record", { id: proposalId })
            }
          />
        )}
      </PageBody>
    </Page>
  );
}

export interface RoundComparisonGridProps {
  topics: readonly ComparisonTopic[];
  proposals: readonly ComparisonProposal[];
  answers: readonly ComparisonAnswer[];
  proposalHref?: (proposalId: string) => string | undefined;
}

/** Addon-local transposed grid: topic/fact rows by readable proposal columns. */
export function RoundComparisonGrid({
  topics,
  proposals,
  answers,
  proposalHref,
}: RoundComparisonGridProps): React.ReactElement {
  const t = useProposalsT();
  const rows = React.useMemo(() => comparisonRows(topics), [topics]);
  return (
    <div className="h-full overflow-auto">
      <Table
        className="min-w-max"
        aria-label={t("comparison.gridLabel")}
        aria-colcount={proposals.length + 1}
        aria-rowcount={rows.length + 1}
      >
        <TableHeader>
          <TableRow>
            <TableHead
              sticky
              scope="col"
              className="left-0 z-sticky-col h-auto min-w-56 border-r border-border-subtle px-4 py-3 text-13 text-fg"
            >
              {t("comparison.subject")}
            </TableHead>
            {proposals.map((proposal) => (
              <ProposalHeader
                key={proposal.id}
                proposal={proposal}
                href={proposalHref?.(proposal.id)}
              />
            ))}
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((row) => (
            <TableRow key={row.id}>
              <ComparisonRowHeader row={row} t={t} />
              {proposals.map((proposal) => (
                <TableCell
                  key={`${row.id}:${proposal.id}`}
                  className="h-auto min-w-72 border-r border-border-subtle bg-canvas px-4 py-3 text-13 text-fg"
                >
                  <ComparisonCell
                    row={row}
                    proposal={proposal}
                    answers={answers}
                  />
                </TableCell>
              ))}
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

function ProposalHeader({
  proposal,
  href,
}: {
  proposal: ComparisonProposal;
  href?: string;
}): React.ReactElement {
  const label = proposalColumnLabel(proposal);
  const content = href ? <TextLink href={href}>{label}</TextLink> : label;
  return (
    <TableHead
      sticky
      scope="col"
      className="h-auto min-w-72 border-r border-border-subtle px-4 py-3 text-left"
    >
      <div className="flex min-h-10 items-center justify-between gap-3">
        <span className="min-w-0 truncate text-13 font-semibold text-fg">
          {content}
        </span>
        <FieldDescriptorControl
          field={{ name: "state", widget: "statusBadge" }}
          value={proposal.state}
          row={proposal}
          readOnly
        />
      </div>
    </TableHead>
  );
}

function ComparisonRowHeader({
  row,
  t,
}: {
  row: ComparisonRow;
  t: ProposalsT;
}): React.ReactElement {
  const label =
    row.kind === "topic"
      ? String(row.topic.name ?? row.topic.key ?? "")
      : factLabel(row.field, t);
  return (
    <TableHead
      scope="row"
      className="sticky left-0 z-sticky-col h-auto min-w-56 border-r border-border-subtle bg-sheet px-4 py-3 align-top text-fg"
    >
      <div className="text-13 font-semibold text-fg">{label}</div>
      {row.kind === "topic" && !isEmptyComparisonValue(row.topic.hint) ? (
        <div className="mt-1 text-xs text-fg-muted">
          <FieldDescriptorControl
            field={{ name: "hint", widget: "markdown.preview" }}
            value={row.topic.hint}
            row={row.topic}
            readOnly
          />
        </div>
      ) : null}
    </TableHead>
  );
}

function ComparisonCell({
  row,
  proposal,
  answers,
}: {
  row: ComparisonRow;
  proposal: ComparisonProposal;
  answers: readonly ComparisonAnswer[];
}): React.ReactElement {
  const value = comparisonCellValue(row, proposal, answers);
  if (isEmptyComparisonValue(value)) return <>—</>;
  const field =
    row.kind === "topic"
      ? { name: "body", widget: "markdown.preview" }
      : FACT_DESCRIPTORS[row.field];
  return (
    <FieldDescriptorControl field={field} value={value} row={proposal} readOnly />
  );
}

function factLabel(field: ComparisonFactField, t: ProposalsT): string {
  const keys: Record<ComparisonFactField, string> = {
    cost: "comparison.fact.cost",
    staffing: "comparison.fact.staffing",
    timeframe_start: "comparison.fact.timeframeStart",
    timeframe_end: "comparison.fact.timeframeEnd",
    confidence: "comparison.fact.confidence",
    valid_until: "comparison.fact.validUntil",
  };
  return t(keys[field]);
}
