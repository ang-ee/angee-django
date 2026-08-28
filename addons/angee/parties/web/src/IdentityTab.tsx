import * as React from "react";
import {
  ListView,
  Tag,
  type ListColumn,
  type RecordPanelContext,
} from "@angee/ui";

import { usePartiesT } from "./i18n";
import {
  usePartyHandleRowActions,
  type PartyHandleActionRow,
} from "./party-handle-row-actions";

type LinkRow = PartyHandleActionRow & {
  confidence?: number;
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
export function IdentityTab({ recordId }: RecordPanelContext): React.ReactElement {
  const t = usePartiesT();
  const rowActions = usePartyHandleRowActions<LinkRow>("remaining");

  const columns = React.useMemo<readonly ListColumn<LinkRow>[]>(
    () => [
      { field: "handle.value", header: t("identity.handle") },
      { field: "handle.platform", header: t("identity.platform") },
      { field: "confidence" },
      { field: "source" },
      {
        field: "is_confirmed",
        header: t("identity.state"),
        render: (row) => linkState(row, t),
      },
    ],
    [t],
  );

  return (
    <ListView<LinkRow>
      resource="parties.PartyHandle"
      scope="local"
      fields={[
        "id",
        "handle.value",
        "handle.platform",
        "confidence",
        "source",
        "is_confirmed",
        "is_dismissed",
      ]}
      baseFilter={{ party: { exact: recordId } }}
      columns={columns}
      rowActions={rowActions}
      emptyContent={t("identity.empty")}
    />
  );
}
