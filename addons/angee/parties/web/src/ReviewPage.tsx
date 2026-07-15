import * as React from "react";
import {
  Button,
  Glyph,
  ListView,
  Page,
  PageBody,
  PageHeader,
  type ListColumn,
  type StringIdRow,
  useAuthoredResourceMutation,
} from "@angee/ui";
import {
  ConfirmPartyHandle,
  DismissPartyHandle,
  PARTY_HANDLE_DECISION_INVALIDATES,
} from "./documents";
import { usePartiesT } from "./i18n";

type SuggestionRow = StringIdRow;

/**
 * The review queue: every low-confidence, undecided party↔handle claim across
 * the directory. Nothing merges silently — confirming sets full
 * confidence, dismissing writes the durable anti-link.
 */
export function ReviewPage(): React.ReactElement {
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

  const columns = React.useMemo<readonly ListColumn<SuggestionRow>[]>(
    () => [
      { field: "handle.value", header: t("review.handle") },
      { field: "handle.platform", header: t("review.platform") },
      { field: "party.display_name", header: t("review.party") },
      { field: "confidence" },
      { field: "source" },
      {
        field: "id",
        header: t("review.actions"),
        headerVisuallyHidden: true,
        sortable: false,
        align: "right",
        render: (row) => (
          <span className="inline-flex gap-1">
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
          </span>
        ),
      },
    ],
    [busy, confirm, dismiss, t],
  );

  return (
    <Page>
      <PageHeader title={t("review.title")} description={t("review.description")} />
      <PageBody>
        <ListView<SuggestionRow>
          resource="parties.PartyHandle"
          fields={[
            "id",
            "handle.value",
            "handle.platform",
            "party.display_name",
            "confidence",
            "source",
          ]}
          baseFilter={{
            is_confirmed: { exact: false },
            is_dismissed: { exact: false },
            confidence: { lt: 0.5 },
          }}
          columns={columns}
          emptyContent={t("review.empty")}
        />
      </PageBody>
    </Page>
  );
}
