import * as React from "react";
import {
  Action,
  Column,
  ResourceList,
  Facet,
  Field,
  Form,
  Group,
  List,
} from "@angee/ui";

const MODEL = "messaging.Message";

// Default the inbox to a by-status grouping. Hoisted to a stable reference so the
// list does not re-seed its grouping on every render.
const DEFAULT_GROUPS = { list: { field: "status" } } as const;

/**
 * The inbox: cross-thread "smart aggregation" over messages. Channel is an
 * explicit high-cardinality facet because it is useful here but not rendered as
 * a column. The list groups by status/channel through `ResourceList` +
 * `ListView`, not a hand-rolled inbox. Messages arrive via channel sync,
 * so the list creates nothing; status is the one human-editable field.
 */
export function MessagesPage(): React.ReactElement {
  return (
    <ResourceList resource={MODEL} placement="inline" routed hideCreate>
      <List
        resource={MODEL}
        defaultGroups={DEFAULT_GROUPS}
      >
        <Facet field="channel" label="Channel" labelField="display_name" />
        <Column field="subject" />
        <Column field="sender.value" header="Sender" />
        <Column field="thread.subject" header="Thread" />
        <Column field="status" widget="statusBadge" />
        <Column field="sent_at" />
      </List>
      <Form resource={MODEL}>
        <Field name="subject" readOnly />
        {/* status reads the UPPERCASE enum member name but its String patch input
            takes the lowercase value, so moderation rides declarative verbs (which
            write the value) rather than an editable enum field. */}
        <Field name="status" readOnly />
        <Group label="Envelope" columns={2}>
          <Field name="platform" readOnly />
          <Field name="direction" readOnly />
          <Field name="sent_at" readOnly />
          <Field name="external_id" readOnly />
        </Group>
        <Field name="preview" readOnly />
        <Action
          id="hide"
          label="Hide"
          set={{ status: "hidden" }}
          visibleWhen={(record) => record.status !== "HIDDEN" && record.status !== "REMOVED"}
        />
        <Action
          id="remove"
          label="Remove"
          danger
          confirm={{ title: "Remove message?", body: "It is hidden from the inbox until restored.", danger: true }}
          set={{ status: "removed" }}
          visibleWhen={(record) => record.status !== "REMOVED"}
        />
        <Action
          id="restore"
          label="Restore"
          set={{ status: "synced" }}
          visibleWhen={(record) => record.status === "HIDDEN" || record.status === "REMOVED"}
        />
      </Form>
    </ResourceList>
  );
}
