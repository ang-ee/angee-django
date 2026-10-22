import * as React from "react";
import { useAuthoredQuery } from "@angee/refine";
import { Chip, InlineEmpty, MiniCard, RailPanel, StatusBadge, formatDateTime, optionLabel, useEnumOptions } from "@angee/ui";

import { CHANNEL_MODEL, MessagingChannelHealth } from "./documents";
import { useMessagingT } from "./i18n";

const CHANNEL_LIMIT = 20;

/** Messaging-owned channel health contributed into the Parties overview seam. Every
 *  state reads through the channel's own enum labels and the composed status tones. */
export function MessagingOverviewContribution(): React.ReactElement {
  const t = useMessagingT();
  const pairingStates = useEnumOptions(CHANNEL_MODEL, "pairing_state", { casing: "upper" });
  const syncStages = useEnumOptions(CHANNEL_MODEL, "sync_stage", { casing: "upper" });
  const backends = useEnumOptions(CHANNEL_MODEL, "backend_class");
  const query = useAuthoredQuery(
    MessagingChannelHealth,
    { limit: CHANNEL_LIMIT },
    { models: [CHANNEL_MODEL] },
  );
  const channels = query.data?.channels ?? [];
  const total = query.data?.channels_aggregate.aggregate?.count ?? channels.length;
  return (
    <RailPanel title={t("overview.channels.title")} count={total} fetching={query.isFetching}>
      {channels.length > 0 ? (
        <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-3">
          {channels.map((channel) => {
            const unhealthy = Boolean(channel.sync_error) || channel.sync_stage === "FAILED";
            return (
              <MiniCard
                key={channel.id}
                title={channel.display_name}
                meta={
                  channel.last_sync_completed_at
                    ? formatDateTime(channel.last_sync_completed_at)
                    : t("overview.channels.neverSynced")
                }
                tags={
                  <>
                    {channel.pairing_state ? <StatusBadge value={channel.pairing_state} options={pairingStates} /> : null}
                    <Chip tone="neutral">{optionLabel(backends, channel.backend_class)}</Chip>
                    {unhealthy ? <Chip tone="danger">{t("overview.channels.needsAttention")}</Chip> : null}
                    {channel.sync_stage ? <StatusBadge value={channel.sync_stage} options={syncStages} display="text" /> : null}
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
