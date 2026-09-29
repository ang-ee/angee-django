import { FieldDescriptorControl, Table, TableBody, TableCell, TableHead, TableHeader, TableRow, TextLink, canonicalOptionValue, optionLabel, useEnumOptions, type FieldDescriptor } from "@angee/ui";
import * as React from "react";
import type { ComparisonAnswer, ComparisonProposal, ComparisonTopic } from "./comparison-data";
import { comparisonCellValue, comparisonRows, isEmptyComparisonValue, proposalColumnLabel, type ComparisonFactField, type ComparisonRow } from "./comparison-model";
import { useProposalsT, type ProposalsT } from "./i18n";
import { ANSWER_MODEL } from "./resources";

const FACT_DESCRIPTORS: Readonly<Record<ComparisonFactField, FieldDescriptor>> = {
  statement: { name: "statement", widget: "markdown.preview" },
  cost: { name: "cost", widget: "money", currencyField: "currency" },
  staffing: { name: "staffing", widget: "text" },
  timeframe_start: { name: "timeframe_start", widget: "date" },
  timeframe_end: { name: "timeframe_end", widget: "date" },
  confidence: { name: "confidence", widget: "statusBadge" },
  valid_until: { name: "valid_until", widget: "date" },
};

export interface RoundComparisonGridProps {
  topics: readonly ComparisonTopic[];
  proposals: readonly ComparisonProposal[];
  answers: readonly ComparisonAnswer[];
  facts?: readonly ComparisonFactField[];
  renderColumnHeader?: (proposal: ComparisonProposal) => React.ReactNode;
  renderCell?: (row: ComparisonRow, proposal: ComparisonProposal, answer: ComparisonAnswer | undefined) => React.ReactNode;
  labels?: { subject?: string; grid?: string; facts?: Partial<Record<ComparisonFactField, string>> };
  proposalHref?: (proposalId: string) => string | undefined;
}

/** Addon-local transposed grid: topic/fact rows by readable proposal columns. */
export function RoundComparisonGrid({
  topics,
  proposals,
  answers,
  proposalHref, facts, renderColumnHeader, renderCell, labels,
}: RoundComparisonGridProps): React.ReactElement {
  const t = useProposalsT();
  const rows = React.useMemo(() => comparisonRows(topics, facts), [topics, facts]);
  return (
    <div className="h-full overflow-auto">
      <Table
        className="min-w-max"
        aria-label={labels?.grid ?? t("comparison.gridLabel")}
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
              {labels?.subject ?? t("comparison.subject")}
            </TableHead>
            {proposals.map((proposal) => (
              <ProposalHeader
                key={proposal.id}
                proposal={proposal}
                href={proposalHref?.(proposal.id)}
                content={renderColumnHeader?.(proposal)}
                answers={answers.filter((answer) => answer.proposal?.id === proposal.id)}
              />
            ))}
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((row) => (
            <TableRow key={row.id}>
              <ComparisonRowHeader row={row} t={t} labels={labels?.facts} />
              {proposals.map((proposal) => (
                <TableCell
                  key={`${row.id}:${proposal.id}`}
                  className="h-auto min-w-72 border-r border-border-subtle bg-canvas px-4 py-3 text-13 text-fg"
                >
                  {renderCell ? renderCell(row, proposal, row.kind === "topic" ? answers.find(
                    (answer) => answer.proposal?.id === proposal.id && answer.topic?.id === row.topic.id,
                  ) : undefined) : <ComparisonCell
                    row={row}
                    proposal={proposal}
                    answers={answers}
                  />}
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
  href, content, answers,
}: {
  proposal: ComparisonProposal;
  href?: string;
  content?: React.ReactNode;
  answers: readonly ComparisonAnswer[];
}): React.ReactElement {
  const t = useProposalsT();
  const visibilityOptions = useEnumOptions(ANSWER_MODEL, "visibility", { casing: "upper" });
  const visibility = [...new Set(answers.flatMap((answer) => {
    const selected = canonicalOptionValue(visibilityOptions, answer.visibility);
    const label = selected ? optionLabel(visibilityOptions, selected) : answer.visibility;
    return [...(label ? [label] : []), ...(answer.shared_with_responders === true ? [t("comparison.visibility.shared")] : [])];
  }))].map(String).join(" · ");
  const label = proposalColumnLabel(proposal);
  const header = content !== undefined ? content : (href ? <TextLink href={href}>{label}</TextLink> : label);
  return (
    <TableHead
      sticky
      scope="col"
      className="h-auto min-w-72 border-r border-border-subtle px-4 py-3 text-left"
    >
      <div className="flex min-h-10 items-center justify-between gap-3">
        <span className="min-w-0 truncate text-13 font-semibold text-fg">
          {header}
        </span>
        <FieldDescriptorControl
          field={{ name: "state", widget: "statusBadge" }}
          value={proposal.state}
          row={proposal}
          readOnly
        />
      </div>
      {visibility ? <div className="text-xs font-normal text-fg-muted">{t("comparison.visibility", { visibility })}</div> : null}
    </TableHead>
  );
}

function ComparisonRowHeader({
  row,
  t, labels,
}: {
  row: ComparisonRow;
  t: ProposalsT;
  labels?: Partial<Record<ComparisonFactField, string>>;
}): React.ReactElement {
  const label =
    row.kind === "topic"
      ? String(row.topic.name ?? row.topic.key ?? "")
      : labels?.[row.field] ?? factLabel(row.field, t);
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
    statement: "comparison.fact.statement",
    cost: "comparison.fact.cost",
    staffing: "comparison.fact.staffing",
    timeframe_start: "comparison.fact.timeframeStart",
    timeframe_end: "comparison.fact.timeframeEnd",
    confidence: "comparison.fact.confidence",
    valid_until: "comparison.fact.validUntil",
  };
  return t(keys[field]);
}
