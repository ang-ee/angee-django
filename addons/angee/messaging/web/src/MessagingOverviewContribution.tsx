import * as React from "react";
import { useAuthoredQuery } from "@angee/refine";
import {
  Chip,
  InlineEmpty,
  MiniCard,
  RailPanel,
  RelativeTime,
  statusTone,
  type Tone,
} from "@angee/ui";

import { MessagingChannelHealth } from "./documents";
import { useMessagingT, type MessagingT } from "./i18n";

const CHANNEL_LIMIT = 20;

interface PairingHealthDefinition {
  label: string;
  tone?: Tone;
}

const PAIRING_HEALTH_BY_STATE: Readonly<Record<string, PairingHealthDefinition>> = {
  PAIRED: { label: "overview.channels.paired", tone: "success" },
  LOGGED_OUT: { label: "overview.channels.loggedOut", tone: "danger" },
  PAUSED: { label: "overview.channels.paused" },
  DUPLICATE_ACCOUNT: { label: "overview.channels.duplicate", tone: "danger" },
  AWAITING_SCAN: { label: "overview.channels.awaitingScan", tone: "info" },
  AWAITING_PASSWORD: {
    label: "overview.channels.awaitingPassword",
    tone: "warning",
  },
  STARTING: { label: "overview.channels.starting" },
  STOPPED: { label: "overview.channels.stopped" },
};

function pairingHealth(
  state: string | null | undefined,
  t: MessagingT,
): { label: string; tone: Tone } {
  const definition = state ? PAIRING_HEALTH_BY_STATE[state] : undefined;
  const overrides = state && definition?.tone
    ? { [state]: definition.tone }
    : undefined;
  return {
    label: t(definition?.label ?? "overview.channels.notApplicable"),
    tone: statusTone(state, overrides),
  };
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
