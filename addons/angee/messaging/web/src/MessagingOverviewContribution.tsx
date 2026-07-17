import * as React from "react";
import { useAuthoredQuery } from "@angee/refine";
import { Chip, InlineEmpty, MiniCard, RailPanel, RelativeTime } from "@angee/ui";

import { MessagingChannelHealth } from "./documents";
import { useMessagingT, type MessagingT } from "./i18n";

const CHANNEL_LIMIT = 20;

function pairingHealth(
  state: string | null | undefined,
  t: MessagingT,
): { label: string; tone: "success" | "warning" | "danger" | "info" | "neutral" } {
  switch (state) {
    case "PAIRED":
      return { label: t("overview.channels.paired"), tone: "success" };
    case "LOGGED_OUT":
      return { label: t("overview.channels.loggedOut"), tone: "danger" };
    case "PAUSED":
      return { label: t("overview.channels.paused"), tone: "warning" };
    case "DUPLICATE_ACCOUNT":
      return { label: t("overview.channels.duplicate"), tone: "danger" };
    case "AWAITING_SCAN":
      return { label: t("overview.channels.awaitingScan"), tone: "info" };
    case "AWAITING_PASSWORD":
      return { label: t("overview.channels.awaitingPassword"), tone: "warning" };
    case "STARTING":
      return { label: t("overview.channels.starting"), tone: "info" };
    case "STOPPED":
      return { label: t("overview.channels.stopped"), tone: "warning" };
    default:
      return { label: t("overview.channels.notApplicable"), tone: "neutral" };
  }
}

/** Messaging-owned channel health contributed into the Parties overview seam. */
export function MessagingOverviewContribution(): React.ReactElement {
  const t = useMessagingT();
  const query = useAuthoredQuery(
    MessagingChannelHealth,
    { limit: CHANNEL_LIMIT },
    { models: ["messaging.Channel"] },
  );
  const channels = query.data?.channels ?? [];
  const total = query.data?.channels_aggregate.aggregate?.count ?? channels.length;
  return (
    <RailPanel title={t("overview.channels.title")} count={total} fetching={query.fetching}>
      {channels.length > 0 ? (
        <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-3">
          {channels.map((channel) => {
            const syncStatus = String(channel.last_sync_status ?? "").toLowerCase();
            const unhealthy = Boolean(channel.sync_error) || syncStatus === "error";
            const pairing = pairingHealth(channel.pairing_state, t);
            return (
              <MiniCard
                key={channel.id}
                title={channel.display_name}
                meta={
                  channel.last_sync_completed_at
                    ? <RelativeTime value={channel.last_sync_completed_at} />
                    : t("overview.channels.neverSynced")
                }
                primaryTag={{
                  label: pairing.label,
                  tone: pairing.tone,
                }}
                tags={
                  <>
                    <Chip tone="neutral">{channel.backend_class}</Chip>
                    {unhealthy ? <Chip tone="danger">{t("overview.channels.needsAttention")}</Chip> : null}
                    {syncStatus ? <Chip tone="muted">{syncStatus}</Chip> : null}
                  </>
                }
              />
            );
          })}
        </div>
      ) : (
        <InlineEmpty label={t("overview.channels.empty")} />
      )}
    </RailPanel>
  );
}
