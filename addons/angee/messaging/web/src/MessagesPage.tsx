import * as React from "react";
import { Column, Facet, List, ResourceList, type StringIdRow } from "@angee/ui";

import { messageForm, messageRecordTabs, messageSubject } from "./MessageForm";
import { useMessagingT } from "./i18n";

const MODEL = "messaging.Message";
// Default Messages to a by-channel grouping. Hoisted to a stable reference so
// the list does not re-seed its grouping on every render.
const DEFAULT_GROUPS = { list: { field: "channel" } } as const;

interface MessageListRow extends StringIdRow {
  title?: unknown;
}

/**
 * The message corpus across threads. Channel is an
 * explicit high-cardinality facet because it is useful here but not rendered as
 * a column. The list groups by relation label axes through `ResourceList` +
 * `ListView`, not a hand-rolled inbox. Messages arrive via channel sync,
 * so the list creates nothing; status is the one human-editable field. The
 * message title is a server-resolved projection of its TITLE part's fragment.
 */
export function MessagesPage(): React.ReactElement {
  const t = useMessagingT();
  const recordTabs = React.useMemo(() => messageRecordTabs(t), [t]);
  return (
    <ResourceList<MessageListRow> resource={MODEL} form={messageForm} placement="inline" routed hideCreate recordTabs={recordTabs}>
      <List<MessageListRow>
        resource={MODEL}
        defaultGroups={DEFAULT_GROUPS}
      >
        <Facet field="channel" label={t("messages.channel")} />
        <Column<MessageListRow>
          field="title"
          header={t("messages.title")}
          render={(row) => messageSubject(row.title, t("messages.noSubject"))}
        />
        <Column
          field="sender_name"
          header={t("messages.sender")}
        />
        <Column field="thread_title" header={t("messages.thread")} />
        {/* A Feed is also a Channel; the vendor names its platform, while the
            Channel backend_class identifies its parent transport kind. */}
        <Column field="channel_vendor_name" header={t("messages.channelType")} />
        <Column field="status" widget="statusBadge" />
        <Column field="sent_at" />
        <Column field="tags" hiddenByDefault />
      </List>
    </ResourceList>
  );
}
