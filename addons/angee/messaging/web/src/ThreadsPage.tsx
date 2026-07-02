import * as React from "react";
import { Action, Column, ResourceList, Facet, Field, Form, Group, List, type RecordTabDescriptor } from "@angee/ui";

import { ThreadTranscript } from "./ThreadTranscript";

const MODEL = "messaging.Thread";

const threadRecordTabs: readonly RecordTabDescriptor[] = [
  {
    id: "conversation",
    // A record-detail tab is a chat-like surface, so the transcript reads as a
    // conversation (newest at the bottom, scrolled to the latest turn on open). A
    // mail-like aside placement would compose the same view with `order="history"`.
    label: "Conversation",
    render: (context) => <ThreadTranscript threadId={context.recordId} order="conversation" />,
  },
];

/**
 * Threads: grouped list + detail, the detail carrying the thread's Messages.
 * Channel is an explicit facet because it is a useful thread axis but not shown
 * as a list column.
 */
export function ThreadsPage(): React.ReactElement {
  return (
    <ResourceList resource={MODEL} placement="inline" routed hideCreate recordTabs={threadRecordTabs}>
      <List resource={MODEL}>
        <Facet field="channel" label="Channel" labelField="display_name" />
        <Column field="subject" />
        <Column field="modality" />
        <Column field="message_count" header="Messages" />
        <Column field="last_message_at" />
      </List>
      <Form resource={MODEL}>
        <Field name="subject" />
        <Group label="About" columns={2}>
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
          label="Make private"
          set={{ visibility: "private" }}
          visibleWhen={(record) => record.visibility !== "PRIVATE"}
        />
        <Action
          id="vis-unlisted"
          label="Make unlisted"
          set={{ visibility: "unlisted" }}
          visibleWhen={(record) => record.visibility !== "UNLISTED"}
        />
        <Action
          id="vis-public"
          label="Make public"
          set={{ visibility: "public" }}
          visibleWhen={(record) => record.visibility !== "PUBLIC"}
        />
        <Action
          id="vis-restricted"
          label="Make restricted"
          set={{ visibility: "restricted" }}
          visibleWhen={(record) => record.visibility !== "RESTRICTED"}
        />
      </Form>
    </ResourceList>
  );
}
