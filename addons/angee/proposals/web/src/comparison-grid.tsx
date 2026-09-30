import { holdsPermission } from "@angee/metadata";
import { extractActionOutcome } from "@angee/refine";
import {
  Avatar, Badge, Button, FieldDescriptorControl, FormView, Glyph, Select, Table, TableBody,
  TableCell, TableHead, TableHeader, TableRow, Textarea, TextLink, VisibilityControl,
  avatarInitials, canonicalOptionValue, errorMessage, optionLabel, useAuthoredResourceMutation,
  useEnumOptions, useRuntimeAuth, useRuntimeViewAs, type FieldDescriptor, type WidgetOption,
} from "@angee/ui";
import * as React from "react";

import type { ComparisonAnswer, ComparisonProposal, ComparisonTopic } from "./comparison-data";
import { useComparisonAnswerWrite } from "./comparison-writes";
import {
  comparisonCellValue, comparisonRows, isEmptyComparisonValue, proposalColumnLabel,
  proposalIsOwn, type ComparisonFactField, type ComparisonRow,
} from "./comparison-model";
import { ANSWER_VISIBILITY } from "./documents";
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
  labels?: { heading?: string; hint?: string; audience?: string };
  proposalHref?: (proposalId: string) => string | undefined;
}

/** Topic rows by exactly the proposal columns returned by the server. */
export function RoundComparisonGrid({
  topics, proposals, answers, proposalHref, facts, renderColumnHeader, renderCell, labels,
}: RoundComparisonGridProps): React.ReactElement {
  const t = useProposalsT();
  const { user } = useRuntimeAuth();
  const preview = useRuntimeViewAs();
  const visibilityOptions = useEnumOptions(ANSWER_MODEL, "visibility");
  const rows = React.useMemo(() => comparisonRows(topics, facts), [topics, facts]);
  const writableIndex = (row: ComparisonRow): number => row.kind === "topic" && !preview.viewAs && !preview.pending
    ? proposals.findIndex((proposal) => {
      const answer = answerFor(row, proposal, answers);
      return proposalIsOwn(proposal, user?.id) && holdsPermission(proposal, "write")
        && (!answer || holdsPermission(answer, "write"));
    }) : -1;

  const cell = (row: ComparisonRow, proposal: ComparisonProposal): React.ReactNode => {
    const answer = answerFor(row, proposal, answers);
    if (renderCell) return renderCell(row, proposal, answer);
    if (row.kind === "topic") {
      const own = proposalIsOwn(proposal, user?.id) && !preview.viewAs && !preview.pending
        && holdsPermission(proposal, "write") && (!answer || holdsPermission(answer, "write"));
      return own
        ? <EditableAnswerCell key={answer?.id ?? row.id} row={row} proposal={proposal}
            answer={answer} options={visibilityOptions} />
        : <AnswerContent answer={answer} options={visibilityOptions} />;
    }
    const value = comparisonCellValue(row, proposal, answers);
    return isEmptyComparisonValue(value)
      ? <span className="text-fg-muted">{t("comparison.nothingYet")}</span>
      : <FieldDescriptorControl field={FACT_DESCRIPTORS[row.field]} value={value} row={proposal} readOnly />;
  };

  return <section className="space-y-3">
    <FormView.SectionHeading label={labels?.heading ?? t("comparison.heading")}
      hint={labels?.hint ?? t("comparison.headingHint")}
      audience={labels?.audience ?? t("comparison.headingAudience")} />
    <div className="overflow-auto rounded-8 border border-border-subtle">
      <Table className="min-w-max" aria-label={t("comparison.gridLabel")}
        aria-colcount={proposals.length + 1} aria-rowcount={rows.length + 1}>
        <TableHeader><TableRow>
          <TableHead sticky scope="col" className="left-0 z-sticky-col h-auto min-w-56 border-r border-border-subtle px-4 py-3 text-13 text-fg">
            {t("comparison.subject")}
          </TableHead>
          {proposals.map((proposal) => <ProposalHeader key={proposal.id} proposal={proposal}
            href={proposalHref?.(proposal.id)} content={renderColumnHeader?.(proposal)}
            answers={answers.filter((answer) => answer.proposal?.id === proposal.id)}
            options={visibilityOptions} />)}
        </TableRow></TableHeader>
        <TableBody>{rows.map((row) => {
          const emptyTopic = row.kind === "topic" && proposals.every((proposal) =>
            !answerFor(row, proposal, answers));
          const ownIndex = writableIndex(row);
          return <TableRow key={row.id}>
            <ComparisonRowHeader row={row} t={t} />
            {emptyTopic && !renderCell && ownIndex < 0
              ? <TableCell colSpan={proposals.length} className="h-auto border-r border-border-subtle bg-canvas px-4 py-3 text-13 text-fg-muted">
                  {t("comparison.nothingYet")}
                </TableCell>
              : emptyTopic && !renderCell && ownIndex >= 0
                ? <>
                    {ownIndex > 0 ? <TableCell colSpan={ownIndex} className="h-auto border-r border-border-subtle bg-canvas px-4 py-3 text-13 text-fg-muted">
                      {t("comparison.nothingYet")}
                    </TableCell> : null}
                    <TableCell className="h-auto min-w-72 border-r border-border-subtle bg-canvas px-4 py-3 text-13 text-fg">
                      {cell(row, proposals[ownIndex]!)}
                    </TableCell>
                    {ownIndex < proposals.length - 1 ? <TableCell colSpan={proposals.length - ownIndex - 1}
                      className="h-auto border-r border-border-subtle bg-canvas px-4 py-3 text-13 text-fg-muted">
                      {t("comparison.nothingYet")}
                    </TableCell> : null}
                  </>
                : proposals.map((proposal) => <TableCell key={proposal.id}
                    className="h-auto min-w-72 border-r border-border-subtle bg-canvas px-4 py-3 text-13 text-fg">
                    {cell(row, proposal)}
                  </TableCell>)}
          </TableRow>;
        })}</TableBody>
      </Table>
    </div>
  </section>;
}

function answerFor(row: ComparisonRow, proposal: ComparisonProposal, answers: readonly ComparisonAnswer[]): ComparisonAnswer | undefined {
  return row.kind === "topic"
    ? answers.find((answer) => answer.proposal?.id === proposal.id && answer.topic?.id === row.topic.id)
    : undefined;
}

function answerAudience(answer: ComparisonAnswer, options: readonly WidgetOption[], t: ProposalsT): string {
  const label = String(optionLabel(options, answer.visibility == null ? null : String(answer.visibility)));
  return answer.shared_with_responders === true
    ? [label, t("comparison.visibility.shared")].filter(Boolean).join(" · ")
    : label;
}

function ProposalHeader({ proposal, href, content, answers, options }: {
  proposal: ComparisonProposal; href?: string; content?: React.ReactNode;
  answers: readonly ComparisonAnswer[]; options: readonly WidgetOption[];
}): React.ReactElement {
  const t = useProposalsT();
  const label = proposalColumnLabel(proposal, t("comparison.responder"));
  const audiences = [...new Set(answers.map((answer) => answerAudience(answer, options, t)).filter(Boolean))];
  const audience = audiences.length === 1 ? audiences[0]
    : audiences.length > 1 ? t("comparison.visibility.varies") : null;
  return <TableHead sticky scope="col" className="h-auto min-w-72 border-r border-border-subtle px-4 py-2 text-left">
    <div className="flex items-center gap-2">
      <Avatar size="sm" initials={avatarInitials(label)} alt={label} />
      <span className="min-w-0 truncate text-13 font-semibold text-fg">
        {content !== undefined ? content : href ? <TextLink href={href}>{label}</TextLink> : label}
      </span>
    </div>
    {audience ? <div className="ml-8 mt-1 text-xs font-normal text-fg-muted">{t("comparison.visibility", { visibility: audience })}</div> : null}
  </TableHead>;
}

function ComparisonRowHeader({ row, t }: {
  row: ComparisonRow; t: ProposalsT;
}): React.ReactElement {
  const label = row.kind === "topic"
    ? String(row.topic.name ?? row.topic.key ?? "")
    : factLabel(row.field, t);
  return <TableHead scope="row" className="sticky left-0 z-sticky-col h-auto min-w-56 border-r border-border-subtle bg-sheet px-4 py-3 align-top text-fg">
    <div className="text-13 font-semibold text-fg">{label}</div>
    {row.kind === "topic" && !isEmptyComparisonValue(row.topic.hint)
      ? <div className="mt-1 text-xs text-fg-muted">
          <FieldDescriptorControl field={{ name: "hint", widget: "markdown.preview" }}
            value={row.topic.hint} row={row.topic} readOnly />
        </div> : null}
  </TableHead>;
}

function AnswerContent({ answer, options }: {
  answer: ComparisonAnswer | undefined; options: readonly WidgetOption[];
}): React.ReactElement {
  const t = useProposalsT();
  if (!answer || isEmptyComparisonValue(answer.body)) {
    return <div className="space-y-1"><span className="text-fg-muted">{t("comparison.nothingYet")}</span>
      {answer?.visibility != null ? <AnswerAudience answer={answer} options={options} /> : null}
    </div>;
  }
  return <div className="space-y-1">
    <FieldDescriptorControl field={{ name: "body", widget: "markdown.preview" }}
      value={answer.body} row={answer} readOnly />
    {answer.visibility != null ? <AnswerAudience answer={answer} options={options} /> : null}
  </div>;
}

function AnswerAudience({ answer, options }: {
  answer: ComparisonAnswer; options: readonly WidgetOption[];
}): React.ReactElement {
  const t = useProposalsT();
  const preview = useRuntimeViewAs();
  const current = canonicalOptionValue(options, answer.visibility);
  const choices = answer.allowed_visibility ?? [];
  const canChange = !preview.viewAs && !preview.pending && holdsPermission(answer, "manage")
    && typeof answer.revision === "number" && options.some((option) => option.value !== current
      && choices.some((value) => canonicalOptionValue(options, value) === option.value));
  return canChange
    ? <ManageAnswerAudience answer={answer} options={options} />
    : <Badge tone="neutral" density="compact" shape="pill">{answerAudience(answer, options, t)}</Badge>;
}

function ManageAnswerAudience({ answer, options }: {
  answer: ComparisonAnswer; options: readonly WidgetOption[];
}): React.ReactElement {
  const t = useProposalsT();
  const [change, state] = useAuthoredResourceMutation(ANSWER_VISIBILITY, { invalidateModels: [ANSWER_MODEL] });
  return <span className="inline-flex flex-wrap items-center gap-1"><VisibilityControl value={answer.visibility} options={options}
    label={t("comparison.postingTo")} allowedValues={answer.allowed_visibility ?? []}
    readOnly={state.fetching || typeof answer.revision !== "number"}
    onSelect={async (value) => extractActionOutcome(await change({
      answer: answer.id, revision: answer.revision,
      visibility: value.toUpperCase() as "ROUND" | "RESPONDER" | "SEALED",
    }), "set_proposal_answer_visibility")} />
    {answer.shared_with_responders === true ? <Badge tone="neutral" density="compact" shape="pill">
      {t("comparison.visibility.shared")}
    </Badge> : null}
  </span>;
}

function EditableAnswerCell({ row, proposal, answer, options }: {
  row: Extract<ComparisonRow, { kind: "topic" }>; proposal: ComparisonProposal;
  answer: ComparisonAnswer | undefined; options: readonly WidgetOption[];
}): React.ReactElement {
  const t = useProposalsT();
  const subject = String(row.topic.name ?? row.topic.key ?? "");
  const writer = useComparisonAnswerWrite();
  const [editing, setEditing] = React.useState(false);
  const [body, setBody] = React.useState(String(answer?.body ?? ""));
  const [visibility, setVisibility] = React.useState(
    canonicalOptionValue(options, answer?.visibility) ?? options[0]?.value ?? "round",
  );
  const [failure, setFailure] = React.useState<string | null>(null);
  const empty = !answer || isEmptyComparisonValue(answer.body);
  const open = () => {
    setBody(String(answer?.body ?? ""));
    setVisibility(canonicalOptionValue(options, answer?.visibility) ?? options[0]?.value ?? "round");
    setFailure(null);
    setEditing(true);
  };
  if (!editing) {
    return <div className="flex items-start justify-between gap-2">
      {empty
        ? <Button type="button" variant="ghost" size="sm" className="text-fg-muted"
            onClick={open}><Glyph decorative name="pencil" />{t("comparison.writePrompt", { subject: subject.toLowerCase() })}</Button>
        : <AnswerContent answer={answer} options={options} />}
      {empty && answer?.visibility != null ? <AnswerAudience answer={answer} options={options} /> : null}
      {!empty ? <Button type="button" variant="ghost" size="iconSm"
        aria-label={t("comparison.edit", { subject })} onClick={open}>
        <Glyph decorative name="pencil" />
      </Button> : null}
    </div>;
  }
  const choices = answer
    ? options.filter((option) => (answer.allowed_visibility ?? []).some((allowed) =>
      canonicalOptionValue(options, allowed) === option.value))
    : options;
  return <form className="space-y-2" onSubmit={(event) => {
    event.preventDefault();
    if (!body.trim() || writer.pending) return;
    void writer.save(proposal.id, row.topic.id, body.trim(), visibility, answer)
      .then((saved) => { if (saved) { setFailure(null); setEditing(false); } })
      .catch((error) => setFailure(errorMessage(error, t("comparison.saveError"))));
  }}>
    <Textarea value={body} onChange={(event) => setBody(event.target.value)}
      aria-label={t("comparison.writePrompt", { subject })} placeholder={t("comparison.writePrompt", { subject })}
      rows={3} />
    <div className="flex flex-wrap items-center gap-2 text-xs text-fg-muted">
      <span>{t("comparison.postingTo")}</span>
      <Select size="sm" className="min-w-36 w-auto" aria-label={t("comparison.postingTo")}
        options={choices.map((option) => ({ value: option.value, label: option.label }))}
        value={visibility} onValueChange={(value) => setVisibility(value)}
        readOnly={choices.length < 2} />
      <Button type="submit" variant="primary" size="sm" loading={writer.pending}
        disabled={!body.trim() || writer.pending}>{t("comparison.save")}</Button>
      <Button type="button" variant="ghost" size="sm" disabled={writer.pending}
        onClick={() => setEditing(false)}>{t("comparison.cancel")}</Button>
    </div>
    {failure ? <p role="alert" className="text-xs text-danger-text">{failure}</p> : null}
  </form>;
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
