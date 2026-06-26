import * as React from "react";
import {
  Action,
  Column,
  ResourceList,
  Facet,
  Field,
  Form,
  Group,
  ListView,
  List,
  type ListColumn,
  type RecordPanelContext,
  type RecordTabDescriptor,
  type StringIdRow,
} from "@angee/ui";

const MODEL = "messaging.Thread";
const MESSAGE_MODEL = "messaging.Message";

const THREAD_MESSAGE_FIELDS = ["id", "subject", "preview", "status", "sent_at"];

type MessageRow = StringIdRow;

const threadMessageColumns: readonly ListColumn<MessageRow>[] = [
  {
    field: "subject",
    render: (row) => <span className="font-medium text-fg">{String(row.subject ?? "")}</span>,
  },
  {
    field: "preview",
    header: "Preview",
    sortable: false,
    render: (row) => <span className="text-fg-muted">{String(row.preview ?? "")}</span>,
  },
  { field: "status", widget: "statusBadge" },
  { field: "sent_at" },
];

/**
 * The messages in a thread, listed on the thread's detail panel. A local-scoped
 * ListView over the messages resource, filtered to this thread by the Hasura
 * relation ID comparison — the same shared list primitive the routed pages use,
 * server-paginating the whole thread (no client-side first-page truncation).
 */
function ThreadMessagesTab({ recordId }: RecordPanelContext): React.ReactElement {
  return (
    <ListView<MessageRow>
      resource={MESSAGE_MODEL}
      scope="local"
      fields={THREAD_MESSAGE_FIELDS}
      filter={{ thread: { _eq: recordId } }}
      order={{ sent_at: "ASC" }}
      columns={threadMessageColumns}
      rowHref={(row) => `/messaging/inbox/${row.id}`}
      emptyMessage="No messages in this thread yet."
    />
  );
}

const threadRecordTabs: readonly RecordTabDescriptor[] = [
  { id: "messages", label: "Messages", render: (context) => <ThreadMessagesTab {...context} /> },
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
