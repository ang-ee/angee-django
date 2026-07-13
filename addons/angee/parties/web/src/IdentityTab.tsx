import * as React from "react";
import {
  Button,
  Glyph,
  ListView,
  Tag,
  type ListColumn,
  type RecordPanelContext,
  type StringIdRow,
  useAuthoredResourceMutation,
} from "@angee/ui";

import {
  ConfirmPartyHandle,
  DismissPartyHandle,
  PARTY_HANDLE_DECISION_INVALIDATES,
} from "./documents";
import { usePartiesT } from "./i18n";

type LinkRow = StringIdRow & {
  confidence?: number;
  is_confirmed?: boolean;
  is_dismissed?: boolean;
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
  const [confirm, { fetching: confirming }] = useAuthoredResourceMutation(
    ConfirmPartyHandle,
    { invalidateModels: PARTY_HANDLE_DECISION_INVALIDATES },
  );
  const [dismiss, { fetching: dismissing }] = useAuthoredResourceMutation(
    DismissPartyHandle,
    { invalidateModels: PARTY_HANDLE_DECISION_INVALIDATES },
  );
  const busy = confirming || dismissing;

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
      {
        field: "id",
        header: t("review.actions"),
        headerVisuallyHidden: true,
        sortable: false,
        align: "right",
        render: (row) => (
          <span className="inline-flex gap-1">
            {row.is_confirmed ? null : (
              <Button
                variant="ghost"
                size="iconSm"
                aria-label={t("identity.confirm")}
                title={t("identity.confirm")}
                disabled={busy}
                onClick={(event) => {
                  event.stopPropagation();
                  void confirm({ id: row.id });
                }}
              >
                <Glyph name="check" />
              </Button>
            )}
            {row.is_dismissed ? null : (
              <Button
                variant="ghost"
                size="iconSm"
                aria-label={t("identity.dismiss")}
                title={t("identity.dismiss")}
                disabled={busy}
                onClick={(event) => {
                  event.stopPropagation();
                  void dismiss({ id: row.id });
                }}
              >
                <Glyph name="x" />
              </Button>
            )}
          </span>
        ),
      },
    ],
    [busy, confirm, dismiss, t],
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
      emptyContent={t("identity.empty")}
    />
  );
}
