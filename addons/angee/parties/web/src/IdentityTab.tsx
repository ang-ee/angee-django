import * as React from "react";
import {
  ListView,
  Tag,
  NavLink,
  type ListColumn,
  type RecordPanelContext,
  useRouteSearch,
  useResourceRecordHrefLookup,
} from "@angee/ui";

import { usePartiesT } from "./i18n";
import {
  usePartyStatementRowActions,
  type PartyStatementActionRow,
} from "./party-statement-row-actions";

type LinkRow = PartyStatementActionRow & {
  confidence?: number;
  evidence?: Array<{ model: string; id: string }>;
  evidence_truncated?: boolean;
};

function linkState(row: LinkRow, t: ReturnType<typeof usePartiesT>): React.ReactElement {
  if (row.is_dismissed) return <Tag tone="neutral">{t("identity.state.dismissed")}</Tag>;
  if (row.is_confirmed) return <Tag tone="success">{t("identity.state.confirmed")}</Tag>;
  return <Tag tone="warning">{t("identity.state.suggested")}</Tag>;
}

/**
 * The person's identity claims — every handle statement with its confidence and
 * the two review verbs. Confirm outranks any synced score; dismiss is the durable
 * anti-link (the pair is never re-proposed), so both stay visible here instead of
 * silently vanishing.
 */
export function IdentityTab({ recordId, form }: RecordPanelContext): React.ReactElement {
  const t = usePartiesT();
  const rowActions = usePartyStatementRowActions<LinkRow>("remaining");
  const recordHref = useResourceRecordHrefLookup();
  const search = useRouteSearch();
  const focusedHandle = typeof search.partyStatement === "string" ? search.partyStatement : "";

  const columns = React.useMemo<readonly ListColumn<LinkRow>[]>(
    () => [
      { field: "handle.value", header: t("identity.handle") },
      { field: "handle.platform", header: t("identity.platform") },
      { field: "confidence" },
      { field: "source_kind", header: t("identity.claim") },
      {
        field: "evidence",
        header: t("identity.evidence"),
        render: (row) => row.evidence?.length
          ? <span className="flex flex-wrap gap-2">{row.evidence.map((ref, index) => {
            const href = recordHref(ref.model, ref.id);
            return href ? <NavLink key={`${ref.model}:${ref.id}`} href={href} variant="inline">
              {t("identity.evidenceSource", { number: index + 1 })}
            </NavLink> : null;
          })}{row.evidence_truncated ? <span>{t("identity.evidenceTruncated")}</span> : null}</span>
          : t("identity.evidenceUnavailable"),
      },
      {
        field: "is_confirmed",
        header: t("identity.state"),
        render: (row) => linkState(row, t),
      },
    ],
    [recordHref, t],
  );

  return (
    <div className="grid gap-3">
      <p className="text-13 text-fg-muted">{t("identity.authenticationScope")}</p>
      <ListView<LinkRow>
        resource="parties.PartyStatement"
        presentation="embedded"
        fields={[
          "id",
          "handle.value",
          "handle.platform",
          "confidence",
          "source_kind",
          "evidence.model",
          "evidence.id",
          "evidence_truncated",
          "is_confirmed",
          "is_dismissed",
        ]}
        baseFilter={{
          subject: { exact: recordId },
          ...(focusedHandle ? { id: { exact: focusedHandle } } : {}),
        }}
        columns={columns}
        rowActions={form.formReadOnly ? [] : rowActions}
        emptyContent={t("identity.empty")}
      />
    </div>
  );
}
