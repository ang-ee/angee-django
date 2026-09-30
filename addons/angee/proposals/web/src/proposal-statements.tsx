import { holdsPermission } from "@angee/metadata";
import {
  Avatar, Button, EmptyState, FieldDescriptorControl, FormView, Glyph, Skeleton, SkeletonStatus, Textarea, avatarInitials,
  errorMessage, useRuntimeAuth, useRuntimeViewAs,
} from "@angee/ui";
import * as React from "react";

import type { ComparisonProposal } from "./comparison-data";
import { useComparisonStatementWrite } from "./comparison-writes";
import { isEmptyComparisonValue, proposalColumnLabel, proposalIsOwn } from "./comparison-model";
import { useProposalsT } from "./i18n";

export interface ProposalStatementsProps {
  proposals: readonly ComparisonProposal[];
  loading?: boolean;
  labels?: {
    heading?: string;
    hint?: string;
    audience?: string;
  };
}

/** One statement card per readable proposal, hosted separately from the comparison grid. */
export function ProposalStatements({ proposals, loading = false, labels }: ProposalStatementsProps): React.ReactElement {
  const t = useProposalsT();
  const { user } = useRuntimeAuth();
  const preview = useRuntimeViewAs();
  return <section className="space-y-3">
    <FormView.SectionHeading label={labels?.heading ?? t("comparison.statementHeading")}
      hint={labels?.hint ?? t("comparison.statementHint")}
      audience={labels?.audience ?? t("comparison.statementAudience")} />
    {loading && !proposals.length ? <SkeletonStatus label={t("comparison.loading")} className="grid gap-3">
      <Skeleton className="h-28 w-full" /><Skeleton className="h-28 w-full" />
    </SkeletonStatus> : null}
    {!loading && !proposals.length ? <EmptyState title={t("comparison.empty.title")}
      description={t("comparison.empty.description")} /> : null}
    <div className="grid gap-3">
      {proposals.map((proposal) => {
        const own = proposalIsOwn(proposal, user?.id);
        const editable = own && holdsPermission(proposal, "write") && !preview.viewAs && !preview.pending;
        const name = proposalColumnLabel(proposal, t("comparison.responder"));
        return <article key={proposal.id} className="rounded-8 border border-border-subtle bg-sheet p-4">
          <div className="mb-3 flex items-center gap-2">
            <Avatar size="sm" initials={avatarInitials(name)} alt={name} />
            <span className="text-13 font-semibold text-fg">{name}</span>
            <span className="ml-auto text-xs text-fg-muted">
              {own ? t("comparison.statementPrivate") : t("comparison.statementAudience")}
            </span>
          </div>
          {editable
            ? <EditableStatement proposal={proposal} />
            : isEmptyComparisonValue(proposal.statement)
              ? <p className="text-13 text-fg-muted">{t("comparison.statementEmpty")}</p>
              : <FieldDescriptorControl field={{ name: "statement", widget: "markdown.preview" }}
                  value={proposal.statement} row={proposal} readOnly />}
        </article>;
      })}
    </div>
  </section>;
}

function EditableStatement({ proposal }: { proposal: ComparisonProposal }): React.ReactElement {
  const t = useProposalsT();
  const writer = useComparisonStatementWrite();
  const empty = isEmptyComparisonValue(proposal.statement);
  const [editing, setEditing] = React.useState(empty);
  const [body, setBody] = React.useState(String(proposal.statement ?? ""));
  const [failure, setFailure] = React.useState<string | null>(null);
  const prompt = t("comparison.statementPrompt");
  if (!editing) return <div className="flex items-start justify-between gap-2">
    <FieldDescriptorControl field={{ name: "statement", widget: "markdown.preview" }}
      value={proposal.statement} row={proposal} readOnly />
    <Button type="button" variant="ghost" size="iconSm"
      aria-label={t("comparison.statementEdit")}
      onClick={() => { setBody(String(proposal.statement ?? "")); setFailure(null); setEditing(true); }}>
      <Glyph decorative name="pencil" />
    </Button>
  </div>;
  return <form className="space-y-2" onSubmit={(event) => {
    event.preventDefault();
    if (!body.trim() || writer.pending) return;
    void writer.save(proposal, body.trim())
      .then(() => { setFailure(null); setEditing(false); })
      .catch((error) => setFailure(errorMessage(error, t("comparison.statementError"))));
  }}>
    <Textarea value={body} onChange={(event) => setBody(event.target.value)}
      aria-label={prompt} placeholder={prompt} rows={4} />
    <div className="flex items-center gap-2">
      <Button type="submit" variant="primary" size="sm" loading={writer.pending}
        disabled={!body.trim() || writer.pending}>{t("comparison.statementSave")}</Button>
      {!empty ? <Button type="button" variant="ghost" size="sm" disabled={writer.pending}
        onClick={() => setEditing(false)}>{t("comparison.cancel")}</Button> : null}
    </div>
    {failure ? <p role="alert" className="text-xs text-danger-text">{failure}</p> : null}
  </form>;
}
