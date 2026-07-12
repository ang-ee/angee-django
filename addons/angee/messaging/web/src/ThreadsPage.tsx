import * as React from "react";
import { Action, Column, ResourceList, Facet, Field, Form, Group, List, type RecordTabDescriptor } from "@angee/ui";

import { ThreadTranscript } from "./ThreadTranscript";
import { useMessagingT } from "./i18n";

const MODEL = "messaging.Thread";

function threadRecordTabs(
  t: ReturnType<typeof useMessagingT>,
): readonly RecordTabDescriptor[] {
  return [
    {
      id: "conversation",
      // A record-detail tab is a chat-like surface, so the transcript reads as a
      // conversation (newest at the bottom, scrolled to the latest turn on open). A
      // mail-like aside placement would compose the same view with `order="history"`.
      label: t("threads.tabConversation"),
      render: (context) => <ThreadTranscript threadId={context.recordId} order="conversation" />,
    },
  ];
}

/**
 * Threads: grouped list + detail, the detail carrying the thread's Messages.
 * Channel is an explicit facet because it is a useful thread axis but not shown
 * as a list column.
 */
export function ThreadsPage(): React.ReactElement {
  const t = useMessagingT();
  const recordTabs = React.useMemo(() => threadRecordTabs(t), [t]);
  return (
    <ResourceList resource={MODEL} placement="inline" routed hideCreate recordTabs={recordTabs}>
      <List resource={MODEL}>
        <Facet field="channel" label={t("threads.channel")} labelField="display_name" />
        <Column field="title.text" header={t("threads.title")} />
        <Column field="modality" />
        <Column field="message_count" header={t("threads.messageCount")} />
        <Column field="last_message_at" />
      </List>
      <Form resource={MODEL}>
        {/* The title is a pointer at a shared content-addressed fragment, derived
            by the ingest (normalized subject / record label) — not directly editable. */}
        <Group label={t("threads.groupAbout")} columns={2}>
          <Field name="platform" readOnly />
          <Field name="modality" readOnly />
          {/* visibility reads the UPPERCASE enum member name but its String patch
              input takes the lowercase value, so the change rides declarative
              verbs (which write the value) rather than an editable enum field. */}
          <Field name="visibility" readOnly />
          <Field name="message_count" readOnly />
        </Group>
        <Action
          id="vis-private"
          label={t("threads.makePrivate")}
          set={{ visibility: "private" }}
          visibleWhen={(record) => record.visibility !== "PRIVATE"}
        />
        <Action
          id="vis-unlisted"
          label={t("threads.makeUnlisted")}
          set={{ visibility: "unlisted" }}
          visibleWhen={(record) => record.visibility !== "UNLISTED"}
        />
        <Action
          id="vis-public"
          label={t("threads.makePublic")}
          set={{ visibility: "public" }}
          visibleWhen={(record) => record.visibility !== "PUBLIC"}
        />
        <Action
          id="vis-restricted"
          label={t("threads.makeRestricted")}
          set={{ visibility: "restricted" }}
          visibleWhen={(record) => record.visibility !== "RESTRICTED"}
        />
      </Form>
    </ResourceList>
  );
}
