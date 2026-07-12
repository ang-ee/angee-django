import * as React from "react";
import {
  Action,
  Column,
  Facet,
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
} from "@angee/ui";

import { useMessagingT } from "./i18n";

const MODEL = "messaging.Message";
const PART_MODEL = "messaging.Part";

// Default the inbox to a by-channel grouping. Hoisted to a stable reference so
// the list does not re-seed its grouping on every render.
const DEFAULT_GROUPS = { list: { field: "channel.display_name" } } as const;

// The structural tab defaults to grouping the part rows by role (title / header /
// body / quoted / signature); regrouping by fragment.hash through the shared
// grouping chooser turns the same view into the dedup/interconnection lens.
const PART_GROUPS = { list: { field: "role" } } as const;

type PartRow = StringIdRow;

// The nested selection the part columns render from: the part's structural
// facts plus its fragment's identity (kind, hash) and connectivity counts —
// how many parts and messages share that exact text.
const PART_FIELDS = [
  "id",
  "position",
  "role",
  "type",
  "disposition",
  "name",
  "cid",
  "parent.id",
  "fragment.id",
  "fragment.kind",
  "fragment.hash",
  "fragment.text",
  "fragment.part_count",
  "fragment.message_count",
  "file.id",
  "file.filename",
] as const;

type PartFragment = {
  kind?: string | null;
  hash?: string | null;
  text?: string | null;
  part_count?: number | null;
  message_count?: number | null;
} | null;

function fragmentOf(row: PartRow): NonNullable<PartFragment> | null {
  return (row as { fragment?: PartFragment }).fragment ?? null;
}

function partColumns(t: ReturnType<typeof useMessagingT>): readonly ListColumn<PartRow>[] {
  return [
    { field: "position" },
    { field: "role" },
    { field: "type" },
    { field: "name" },
    {
      field: "fragment.hash",
      header: t("parts.fragment"),
      render: (row) => {
        const fragment = fragmentOf(row);
        if (!fragment?.hash) return null;
        return <code className="text-2xs text-fg-subtle">{fragment.hash.slice(0, 10)}</code>;
      },
    },
    {
      field: "fragment.part_count",
      header: t("parts.shared"),
      render: (row) => {
        const fragment = fragmentOf(row);
        if (!fragment?.hash) return null;
        const parts = fragment.part_count ?? 1;
        const messages = fragment.message_count ?? 1;
        if (parts <= 1) return <span className="text-fg-subtle">{t("parts.unique")}</span>;
        return (
          <span className="font-medium">
            {t("parts.sharedBy", { parts: String(parts), messages: String(messages) })}
          </span>
        );
      },
    },
    {
      field: "fragment.text",
      header: t("parts.text"),
      render: (row) => {
        const fragment = fragmentOf(row);
        const file = (row as { file?: { filename?: string | null } | null }).file;
        if (fragment?.text) {
          return <span className="block max-w-96 truncate text-fg">{fragment.text}</span>;
        }
        return file?.filename ? <span className="text-fg-subtle">{file.filename}</span> : null;
      },
    },
  ];
}

/** The message's structural content: its part rows as the shared nested data
 *  view — filter/sort/group chrome included — filtered to this record, grouped
 *  by role by default, regroupable by shared fragment for the dedup lens. */
function MessagePartsTab({ recordId }: RecordPanelContext): React.ReactElement {
  const t = useMessagingT();
  const columns = React.useMemo(() => partColumns(t), [t]);
  return (
    <ListView<PartRow>
      resource={PART_MODEL}
      scope="local"
      fields={PART_FIELDS}
      baseFilter={{ message: { exact: recordId } }}
      columns={columns}
      defaultGroups={PART_GROUPS}
      emptyContent={t("parts.empty")}
    />
  );
}

function messageRecordTabs(
  t: ReturnType<typeof useMessagingT>,
): readonly RecordTabDescriptor[] {
  return [
    {
      id: "content",
      label: t("messages.tabContent"),
      render: (context) => <MessagePartsTab {...context} />,
    },
  ];
}

/**
 * The inbox: cross-thread "smart aggregation" over messages. Channel is an
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
    <ResourceList resource={MODEL} placement="inline" routed hideCreate recordTabs={recordTabs}>
      <List
        resource={MODEL}
        defaultGroups={DEFAULT_GROUPS}
      >
        <Facet field="channel" label={t("messages.channel")} labelField="display_name" />
        <Column field="title" header={t("messages.title")} />
        <Column field="sender" header={t("messages.sender")} />
        <Column field="thread.title.text" header={t("messages.thread")} />
        <Column field="status" widget="statusBadge" />
        <Column field="sent_at" />
      </List>
      <Form resource={MODEL}>
        {/* The record heading: the message's TITLE-part text (its subject). */}
        <Field name="title" title readOnly />
        {/* status reads the UPPERCASE enum member name but its String patch input
            takes the lowercase value, so moderation rides declarative verbs (which
            write the value) rather than an editable enum field. */}
        <Field name="status" readOnly />
        <Group label={t("messages.groupEnvelope")} columns={2}>
          <Field name="platform" readOnly />
          <Field name="direction" readOnly />
          <Field name="sent_at" readOnly />
          <Field name="external_id" readOnly />
        </Group>
        <Field name="preview" readOnly />
        <Action
          id="hide"
          label={t("messages.hide")}
          set={{ status: "hidden" }}
          visibleWhen={(record) => record.status !== "HIDDEN" && record.status !== "REMOVED"}
        />
        <Action
          id="remove"
          label={t("messages.remove")}
          danger
          confirm={{ title: t("messages.removeTitle"), body: t("messages.removeBody"), danger: true }}
          set={{ status: "removed" }}
          visibleWhen={(record) => record.status !== "REMOVED"}
        />
        <Action
          id="restore"
          label={t("messages.restore")}
          set={{ status: "synced" }}
          visibleWhen={(record) => record.status === "HIDDEN" || record.status === "REMOVED"}
        />
      </Form>
    </ResourceList>
  );
}
