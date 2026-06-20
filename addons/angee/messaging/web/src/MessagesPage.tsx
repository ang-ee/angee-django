import * as React from "react";
import {
  Action,
  Column,
  DataPage,
  Field,
  Form,
  Group,
  GroupListView,
  List,
  useRelationFacet,
  type DataToolbarFilterField,
  type DataToolbarFilterOption,
  type DataToolbarGroupOption,
} from "@angee/base";

const MODEL = "messaging.Message";

// Default the inbox to a by-status grouping. Hoisted to a stable reference so the
// list does not re-seed its grouping on every render.
const DEFAULT_GROUPS = { list: { field: "status" } } as const;

/**
 * The inbox: cross-thread "smart aggregation" over messages. Sender / channel /
 * thread are shared `useRelationFacet`s (the same SDL-derived facet any relation
 * gets), and the list groups by status / thread — composed on `DataPage` +
 * `GroupListView`, not a hand-rolled inbox. Messages arrive via channel sync, so
 * the list creates nothing; status is the one human-editable field.
 */
export function MessagesPage(): React.ReactElement {
  const senderFacet = useRelationFacet(MODEL, { field: "sender", label: "Sender", labelField: "value" });
  // Channel has no string title of its own, so label the facet by its displayName
  // (the operator-given name, vendor-derived when unset) rather than the default id.
  const channelFacet = useRelationFacet(MODEL, { field: "channel", label: "Channel", labelField: "displayName" });
  const threadFacet = useRelationFacet(MODEL, { field: "thread", label: "Thread", labelField: "subject" });

  const filters = React.useMemo<readonly DataToolbarFilterOption[]>(
    () => [...senderFacet.filters, ...channelFacet.filters, ...threadFacet.filters],
    [senderFacet.filters, channelFacet.filters, threadFacet.filters],
  );
  const filterFields = React.useMemo<readonly DataToolbarFilterField[]>(
    () => [...senderFacet.filterFields, ...channelFacet.filterFields, ...threadFacet.filterFields],
    [senderFacet.filterFields, channelFacet.filterFields, threadFacet.filterFields],
  );
  const groupOptions = React.useMemo<readonly DataToolbarGroupOption[]>(
    () => [
      ...(threadFacet.groupOption ? [threadFacet.groupOption] : []),
      ...(senderFacet.groupOption ? [senderFacet.groupOption] : []),
      ...(channelFacet.groupOption ? [channelFacet.groupOption] : []),
      { id: "status", label: "Status", group: { field: "status" } },
    ],
    [threadFacet.groupOption, senderFacet.groupOption, channelFacet.groupOption],
  );

  return (
    <DataPage model={MODEL} placement="inline" routed hideCreate>
      <List
        model={MODEL}
        list={GroupListView}
        filters={filters}
        filterFields={filterFields}
        groupOptions={groupOptions}
        defaultGroups={DEFAULT_GROUPS}
      >
        <Column field="subject" />
        <Column field="sender.value" header="Sender" />
        <Column field="thread.subject" header="Thread" />
        <Column field="status" widget="statusBadge" />
        <Column field="sentAt" />
      </List>
      <Form model={MODEL}>
        <Field name="subject" readOnly />
        {/* status reads the UPPERCASE enum member name but its String patch input
            takes the lowercase value, so moderation rides declarative verbs (which
            write the value) rather than an editable enum field. */}
        <Field name="status" readOnly />
        <Group label="Envelope" columns={2}>
          <Field name="platform" readOnly />
          <Field name="direction" readOnly />
          <Field name="sentAt" readOnly />
          <Field name="externalId" readOnly />
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
    </DataPage>
  );
}
