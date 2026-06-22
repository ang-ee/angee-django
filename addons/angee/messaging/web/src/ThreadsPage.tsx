import * as React from "react";
import {
  Action,
  Column,
  DataPage,
  Facet,
  Field,
  Form,
  Group,
  GroupListView,
  List,
  RowsListView,
  type ListColumn,
  type RecordPanelContext,
  type RecordTabDescriptor,
} from "@angee/base";
import { useResourceList, type Row } from "@angee/sdk";

const MODEL = "messaging.Thread";
const MESSAGE_MODEL = "messaging.Message";

const THREAD_MESSAGE_FIELDS = ["id", "subject", "preview", "status", "sentAt"];

// Every resource row selects `id`; narrow to the id-bearing shape RowsListView keys on.
type MessageRow = Row & { id: string };

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
  { field: "sentAt" },
];

/**
 * The messages in a thread, listed on the thread's detail panel. The relation
 * lookup is `sqid` (the SDL filter for the FK), and the shared RowsListView owns
 * the fetching/error/empty states rather than a hand-rolled list.
 */
function ThreadMessagesTab({ recordId }: RecordPanelContext): React.ReactElement {
  const { rows, fetching, error } = useResourceList(MESSAGE_MODEL, {
    filter: { thread: { sqid: recordId } },
    fields: THREAD_MESSAGE_FIELDS,
    order: { sentAt: "ASC" },
  });
  return (
    <RowsListView
      rows={rows as readonly MessageRow[]}
      columns={threadMessageColumns}
      fetching={fetching}
      error={error}
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
    <DataPage model={MODEL} placement="inline" routed hideCreate recordTabs={threadRecordTabs}>
      <List model={MODEL} list={GroupListView}>
        <Facet field="channel" label="Channel" labelField="displayName" />
        <Column field="subject" />
        <Column field="modality" />
        <Column field="messageCount" header="Messages" />
        <Column field="lastMessageAt" />
      </List>
      <Form model={MODEL}>
        <Field name="subject" />
        <Group label="About" columns={2}>
          <Field name="platform" readOnly />
          <Field name="modality" readOnly />
          {/* visibility reads the UPPERCASE enum member name but its String patch
              input takes the lowercase value, so the change rides declarative
              verbs (which write the value) rather than an editable enum field. */}
          <Field name="visibility" readOnly />
          <Field name="messageCount" readOnly />
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
    </DataPage>
  );
}
