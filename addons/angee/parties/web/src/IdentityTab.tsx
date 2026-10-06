import * as React from "react";
import {
  ListView,
  Tag,
  TextLink,
  type ListColumn,
  type RecordPanelContext,
  useRouteSearch,
  useResourceRecordHrefLookup,
} from "@angee/ui";

import { usePartiesT } from "./i18n";
import {
  usePartyHandleRowActions,
  type PartyHandleActionRow,
} from "./party-handle-row-actions";

type LinkRow = PartyHandleActionRow & {
  confidence?: number;
  evidence_refs?: Array<{ model: string; id: string }>;
  evidence_truncated?: boolean;
};

function linkState(row: LinkRow, t: ReturnType<typeof usePartiesT>): React.ReactElement {
  if (row.is_dismissed) return <Tag tone="neutral">{t("identity.state.dismissed")}</Tag>;
  if (row.is_confirmed) return <Tag tone="success">{t("identity.state.confirmed")}</Tag>;
  return <Tag tone="warning">{t("identity.state.suggested")}</Tag>;
}

/**
 * The person's identity claims — every party↔handle link with its confidence and
 * the two review verbs. Confirm outranks any synced score; dismiss is the durable
 * anti-link (the pair is never re-proposed), so both stay visible here instead of
 * silently vanishing.
 */
export function IdentityTab({ recordId, form }: RecordPanelContext): React.ReactElement {
  const t = usePartiesT();
  const rowActions = usePartyHandleRowActions<LinkRow>("remaining");
  const recordHref = useResourceRecordHrefLookup();
  const search = useRouteSearch();
  const focusedHandle = typeof search.partyHandle === "string" ? search.partyHandle : "";

  const columns = React.useMemo<readonly ListColumn<LinkRow>[]>(
    () => [
      { field: "handle.value", header: t("identity.handle") },
      { field: "handle.platform", header: t("identity.platform") },
      { field: "confidence" },
      {
        field: "source",
        header: t("identity.claim"),
        render: (row) => row.source === "EMAIL_MATCH"
          ? t("identity.senderClaim")
          : String(row.source ?? ""),
      },
      {
        field: "evidence_refs",
        header: t("identity.evidence"),
        render: (row) => row.evidence_refs?.length
          ? <span className="flex flex-wrap gap-2">{row.evidence_refs.map((ref, index) => {
            const href = recordHref(ref.model, ref.id);
            return href ? <TextLink key={`${ref.model}:${ref.id}`} href={href}>
              {t("identity.evidenceSource", { number: index + 1 })}
            </TextLink> : null;
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
        resource="parties.PartyHandle"
        presentation="embedded"
        fields={[
          "id",
          "handle.value",
          "handle.platform",
          "confidence",
          "source",
          "evidence_refs.model",
          "evidence_refs.id",
          "evidence_truncated",
          "is_confirmed",
          "is_dismissed",
        ]}
        baseFilter={{
          party: { exact: recordId },
          ...(focusedHandle ? { id: { exact: focusedHandle } } : {}),
        }}
        columns={columns}
        rowActions={form.formReadOnly ? [] : rowActions}
        emptyContent={t("identity.empty")}
      />
    </div>
  );
}
