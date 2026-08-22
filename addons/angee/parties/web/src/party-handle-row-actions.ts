import * as React from "react";
import {
  defineRowAction,
  rowIdVariables,
  type RowActionDeclaration,
  type StringIdRow,
} from "@angee/ui";

import {
  ConfirmPartyHandle,
  DismissPartyHandle,
  PARTY_HANDLE_DECISION_INVALIDATES,
} from "./documents";
import { usePartiesT } from "./i18n";

export interface PartyHandleActionRow extends StringIdRow {
  is_confirmed?: boolean;
  is_dismissed?: boolean;
}

export type PartyHandleActionVisibility = "all" | "remaining";

/** The two identity-decision verbs shared by detail and review lists. */
export function usePartyHandleRowActions<TRow extends PartyHandleActionRow>(
  visibility: PartyHandleActionVisibility,
): readonly RowActionDeclaration<TRow>[] {
  const t = usePartiesT();
  return React.useMemo(
    () => [
      defineRowAction<TRow, typeof ConfirmPartyHandle>({
        kind: "authored",
        id: "confirm-party-handle",
        label: t("identity.confirm"),
        icon: "check",
        variant: "ghost",
        document: ConfirmPartyHandle,
        variables: rowIdVariables,
        invalidateModels: PARTY_HANDLE_DECISION_INVALIDATES,
        toast: {
          title: () => t("identity.confirmError"),
          description: () => t("identity.confirmError"),
        },
        visible: (row) => visibility === "all" || !row.is_confirmed,
        pendingPolicy: "active-row",
      }),
      defineRowAction<TRow, typeof DismissPartyHandle>({
        kind: "authored",
        id: "dismiss-party-handle",
        label: t("identity.dismiss"),
        icon: "x",
        variant: "ghost",
        document: DismissPartyHandle,
        variables: rowIdVariables,
        invalidateModels: PARTY_HANDLE_DECISION_INVALIDATES,
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
