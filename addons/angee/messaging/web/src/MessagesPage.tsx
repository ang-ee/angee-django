import * as React from "react";
import { Column, Facet, List, ResourceList, type StringIdRow } from "@angee/ui";

import { messageForm, messageRecordTabs } from "./MessageForm";
import { MESSAGE_SUMMARY_FIELDS, MessageSummary, type MessageSummaryData } from "./MessageSummary";
import { useMessagingT } from "./i18n";

const MODEL = "messaging.Message";
// Default Messages to a by-channel grouping. Hoisted to a stable reference so
// the list does not re-seed its grouping on every render.
const DEFAULT_GROUPS = { list: { field: "channel" } } as const;

type MessageListRow = StringIdRow & MessageSummaryData;

/**
 * The message corpus across threads, each row read like an email. Channel is an
 * explicit high-cardinality facet and the default grouping, through
 * `ResourceList` + `ListView` rather than a hand-rolled inbox. Messages arrive
 * via channel sync, so the list creates nothing.
 */
export function MessagesPage(): React.ReactElement {
  const t = useMessagingT();
  const recordTabs = React.useMemo(() => messageRecordTabs(t), [t]);
  return (
    <ResourceList<MessageListRow> resource={MODEL} form={messageForm} placement="inline" routed hideCreate recordTabs={recordTabs}>
      <List<MessageListRow>
        resource={MODEL}
        defaultGroups={DEFAULT_GROUPS}
        fields={MESSAGE_SUMMARY_FIELDS}
        tableLayout="fixed"
        headerVisibility="visually-hidden"
      >
        <Facet field="channel" label={t("messages.channel")} />
        <Column<MessageListRow>
          field="sent_at"
          header={t("messages.list")}
          hideable={false}
          render={(row) => <MessageSummary message={row} />}
        />
      </List>
    </ResourceList>
  );
}
