import * as React from "react";
import { Alert, ListView, type ListColumn, type RecordPanelContext, useRouteSearch } from "@angee/ui";

import { usePartiesT } from "./i18n";
import { useIdentityClaimColumns, type IdentityClaimRow } from "./identity-columns";
import { usePartyStatementRowActions } from "./party-statement-row-actions";

/**
 * The party's identity claims — every handle statement with its confidence and
 * the two review verbs. Confirm outranks any synced score; dismiss is the durable
 * anti-link (the pair is never re-proposed), so both stay visible here instead of
 * silently vanishing.
 */
export function IdentityTab({ recordId, form }: RecordPanelContext): React.ReactElement {
  const t = usePartiesT();
  const rowActions = usePartyStatementRowActions<IdentityClaimRow>("remaining");
  const shared = useIdentityClaimColumns<IdentityClaimRow>();
  const search = useRouteSearch();
  const focusedHandle = typeof search.partyStatement === "string" ? search.partyStatement : "";

  const columns = React.useMemo<readonly ListColumn<IdentityClaimRow>[]>(
    () => [
      { field: "handle.value", header: t("identity.handle") },
      { field: "handle.platform", header: t("identity.platform") },
      shared.confidence,
      { field: "source_kind", header: t("identity.claim") },
      shared.evidence,
      shared.state,
    ],
    [shared, t],
  );

  return (
    <div className="grid gap-3">
      <Alert tone="info">{t("identity.authenticationScope")}</Alert>
      <ListView<IdentityClaimRow>
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
