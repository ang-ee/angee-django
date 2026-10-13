import * as React from "react";
import {
  defineRowAction,
  rowIdVariables,
  type RowActionDeclaration,
  type StringIdRow,
} from "@angee/ui";

import {
  ConfirmPartyStatement,
  DismissPartyStatement,
  PARTY_STATEMENT_DECISION_INVALIDATES,
} from "./documents";
import { usePartiesT } from "./i18n";

export interface PartyStatementActionRow extends StringIdRow {
  is_confirmed?: boolean;
  is_dismissed?: boolean;
}

export type PartyStatementActionVisibility = "all" | "remaining";

/** The two identity-decision verbs shared by detail and review lists. */
export function usePartyStatementRowActions<TRow extends PartyStatementActionRow>(
  visibility: PartyStatementActionVisibility,
): readonly RowActionDeclaration<TRow>[] {
  const t = usePartiesT();
  return React.useMemo(
    () => [
      defineRowAction<TRow, typeof ConfirmPartyStatement>({
        kind: "authored",
        id: "confirm-party-statement",
        label: t("identity.confirm"),
        icon: "check",
        variant: "ghost",
        document: ConfirmPartyStatement,
        variables: rowIdVariables,
        invalidateModels: PARTY_STATEMENT_DECISION_INVALIDATES,
        toast: {
          title: () => t("identity.confirmError"),
          description: () => t("identity.confirmError"),
        },
        visible: (row) => visibility === "all" || !row.is_confirmed,
        pendingPolicy: "active-row",
      }),
      defineRowAction<TRow, typeof DismissPartyStatement>({
        kind: "authored",
        id: "dismiss-party-statement",
        label: t("identity.dismiss"),
        icon: "x",
        variant: "ghost",
        document: DismissPartyStatement,
        variables: rowIdVariables,
        invalidateModels: PARTY_STATEMENT_DECISION_INVALIDATES,
        toast: {
          title: () => t("identity.dismissError"),
          description: () => t("identity.dismissError"),
        },
        visible: (row) => visibility === "all" || !row.is_dismissed,
        pendingPolicy: "active-row",
      }),
    ],
    [t, visibility],
  );
}
