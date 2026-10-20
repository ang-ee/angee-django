import { holdsPermission } from "@angee/metadata";
import {
  Avatar, Button, Card, CardContent, CardHeader, EmptyState, FieldDescriptorControl, FormView, Glyph, InlineEmpty, LoadingPanel,
  avatarInitials, textRoleVariants, useRuntimeAuth, useRuntimeViewAs,
} from "@angee/ui";
import * as React from "react";

import type { ComparisonProposal } from "./comparison-data";
import { useComparisonStatementWrite } from "./comparison-writes";
import { isEmptyComparisonValue, proposalColumnLabel, proposalIsOwn } from "./comparison-model";
import { useProposalsT } from "./i18n";
import { InlineBodyEditor } from "./inline-body-editor";

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
    {loading && !proposals.length ? <LoadingPanel message={t("comparison.loading")} density="inline" /> : null}
    {!loading && !proposals.length ? <EmptyState title={t("comparison.empty.title")}
      description={t("comparison.empty.description")} /> : null}
    <div className="grid gap-3">
      {proposals.filter((proposal) => holdsPermission(proposal, "read_offer")).map((proposal) => {
        const own = proposalIsOwn(proposal, user?.id);
        const editable = own && holdsPermission(proposal, "write") && !preview.viewAs && !preview.pending;
        const name = proposalColumnLabel(proposal, t("comparison.responder"));
        return <Card key={proposal.id} render={<article />}>
          <CardHeader className="flex-row items-center gap-2">
            <Avatar size="sm" initials={avatarInitials(name)} alt={name} />
            <span className={textRoleVariants({ role: "title" })}>{name}</span>
            <span className={textRoleVariants({ role: "caption", className: "ml-auto" })}>
              {own ? t("comparison.statementPrivate") : t("comparison.statementAudience")}
            </span>
          </CardHeader>
          <CardContent className="pt-0">
            {editable
              ? <EditableStatement proposal={proposal} />
              : isEmptyComparisonValue(proposal.statement)
                ? <InlineEmpty label={t("comparison.statementEmpty")} />
                : <FieldDescriptorControl field={{ name: "statement", widget: "markdown.preview" }}
                    value={proposal.statement} row={proposal} readOnly />}
          </CardContent>
        </Card>;
      })}
    </div>
  </section>;
}

function EditableStatement({ proposal }: { proposal: ComparisonProposal }): React.ReactElement {
  const t = useProposalsT();
  const writer = useComparisonStatementWrite();
  const empty = isEmptyComparisonValue(proposal.statement);
  const [editing, setEditing] = React.useState(empty);
  if (!editing) return <div className="flex items-start justify-between gap-2">
    <FieldDescriptorControl field={{ name: "statement", widget: "markdown.preview" }}
      value={proposal.statement} row={proposal} readOnly />
    <Button type="button" variant="ghost" size="iconSm"
      aria-label={t("comparison.statementEdit")}
      onClick={() => setEditing(true)}>
      <Glyph decorative name="pencil" />
    </Button>
  </div>;
  return <InlineBodyEditor value={String(proposal.statement ?? "")} label={t("comparison.statementPrompt")} rows={4}
    pending={writer.pending} submitLabel={t("comparison.statementSave")} cancel={!empty}
    fallback={t("comparison.statementError")}
    onSave={async (body) => { await writer.save(proposal, body); setEditing(false); }}
    onCancel={() => setEditing(false)} />;
}
