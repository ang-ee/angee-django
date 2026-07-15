import * as React from "react";
import {
  Action,
  Column,
  Field,
  Form,
  Group,
  List,
  ListView,
  ResourceList,
  type ListColumn,
  type RecordPanelContext,
  type RecordTabDescriptor,
  type StringIdRow,
  useResourceRecordHref,
} from "@angee/ui";

import { useSpacesT } from "./i18n";

const MODEL = "spaces.Group";

type MembershipRow = StringIdRow;
type SpaceThreadRow = StringIdRow;

function membershipColumns(
  t: ReturnType<typeof useSpacesT>,
): readonly ListColumn<MembershipRow>[] {
  return [
    { field: "party.display_name", header: t("group.roster.party") },
    { field: "role", header: t("group.roster.role") },
    { field: "is_confirmed" },
    { field: "source" },
    { field: "created_at" },
  ];
}

function threadColumns(
  t: ReturnType<typeof useSpacesT>,
): readonly ListColumn<SpaceThreadRow>[] {
  return [
    { field: "title.text", header: t("group.threads.title") },
    { field: "message_count", header: t("group.threads.messages") },
    { field: "last_message_at" },
  ];
}

function GroupRosterTab({ recordId, ...context }: RecordPanelContext): React.ReactElement {
  const t = useSpacesT();
  void context;
  return (
    <ListView<MembershipRow>
      resource="spaces.Membership"
      scope="local"
      fields={["id", "party.display_name", "role", "is_confirmed", "source", "created_at"]}
      baseFilter={{ group: { exact: recordId } }}
      columns={membershipColumns(t)}
      emptyContent={t("group.roster.empty")}
    />
  );
}

function GroupThreadsTab({ recordId, ...context }: RecordPanelContext): React.ReactElement {
  const t = useSpacesT();
  const threadHref = useResourceRecordHref("messaging.Thread");
  void context;
  return (
    <ListView<SpaceThreadRow>
      resource="spaces.GroupThread"
      scope="local"
      fields={["id", "title.text", "message_count", "last_message_at"]}
      baseFilter={{ group: { exact: recordId } }}
      columns={threadColumns(t)}
      rowHref={threadHref === undefined ? undefined : (thread) => threadHref(thread.id)}
      emptyContent={t("group.threads.empty")}
    />
  );
}

function groupRecordTabs(t: ReturnType<typeof useSpacesT>): readonly RecordTabDescriptor[] {
  return [
    {
      id: "roster",
      label: t("group.tabs.roster"),
      render: (context) => <GroupRosterTab {...context} />,
    },
    {
      id: "threads",
      label: t("group.tabs.threads"),
      render: (context) => <GroupThreadsTab {...context} />,
    },
  ];
}

/** Shared spaces compose the common resource list, roster list, and messaging thread detail. */
export function SpacesPage(): React.ReactElement {
  const t = useSpacesT();
  const tabs = React.useMemo(() => groupRecordTabs(t), [t]);
  return (
    <ResourceList resource={MODEL} placement="inline" routed recordTabs={tabs}>
      <List resource={MODEL}>
        <Column field="name" />
        <Column field="parent.name" header={t("group.parent")} />
        <Column field="visibility" header={t("group.visibility")} />
        <Column field="created_at" />
      </List>
      <Form resource={MODEL}>
        <Field name="name" title />
        <Group label={t("group.details")} columns={2}>
          <Field name="slug" />
          <Field name="parent" label={t("group.parent")} />
          <Field name="visibility" label={t("group.visibility")} readOnly />
        </Group>
        <Field name="description" />
        <Action
          id="visibility-public"
          label={t("group.makePublic")}
          set={{ visibility: "public" }}
          visibleWhen={(record) => record.visibility !== "PUBLIC"}
        />
        <Action
          id="visibility-private"
          label={t("group.makePrivate")}
          set={{ visibility: "private" }}
          visibleWhen={(record) => record.visibility !== "PRIVATE"}
        />
      </Form>
    </ResourceList>
  );
}
