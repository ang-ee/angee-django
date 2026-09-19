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
  LoadingPanel,
  ErrorBanner,
  MessagePartsView,
  ResourceList,
  registerForm,
  TextLink,
  useResourceRecordHrefLookup,
  type ListColumn,
  type RecordPanelContext,
  type RecordTabDescriptor,
  type RegisteredFormProps,
  type StringIdRow,
} from "@angee/ui";
import { useAuthoredQuery } from "@angee/refine";

import { useMessagingT } from "./i18n";
import { MessageDetailPartsDocument, type PartListRow } from "./documents";

const MODEL = "messaging.Message";
const PART_MODEL = "messaging.Part";
// A part's attachment is a storage.File; its routed record page (breadcrumbs
// included) is the follow target for the attachment cell.
const FILE_MODEL = "storage.File";

// Default the inbox to a by-channel grouping. Hoisted to a stable reference so
// the list does not re-seed its grouping on every render.
const DEFAULT_GROUPS = { list: { field: "channel" } } as const;

// The structural tab defaults to grouping the part rows by role (title / header /
// body / quoted / signature); regrouping by fragment.hash through the shared
// grouping chooser turns the same view into the dedup/interconnection lens.
const PART_GROUPS = { list: { field: "role" } } as const;

type PartRow = PartListRow;
interface MessageListRow extends StringIdRow {
  title?: unknown;
}
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
  "file.title",
] as const;

function partColumns(
  t: ReturnType<typeof useMessagingT>,
  recordHref: ReturnType<typeof useResourceRecordHrefLookup>,
): readonly ListColumn<PartRow>[] {
  return [
    { field: "position" },
    { field: "role" },
    { field: "type" },
    { field: "name" },
    {
      field: "fragment.hash",
      header: t("parts.fragment"),
      render: (row) => {
        const fragment = row.fragment;
        if (!fragment?.hash) return null;
        return <code className="text-2xs text-fg-subtle">{fragment.hash.slice(0, 10)}</code>;
      },
    },
    {
      field: "fragment.part_count",
      header: t("parts.shared"),
      render: (row) => {
        const fragment = row.fragment;
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
        const fragment = row.fragment;
        if (fragment?.text) {
          return <span className="block max-w-96 truncate text-fg">{fragment.text}</span>;
        }
        // An attachment part links to its storage.File record page (breadcrumbs
        // included) so the filename opens the file in-app instead of being dead
        // text — the same follow pattern the activity agenda uses. Degrades to
        // plain text where storage.File has no routed page.
        const file = row.file;
        // Prefer the part's own name: one File is content-addressed and can back
        // parts from many messages, so the per-part name is the reliable one; the
        // shared file title/filename is the fallback. (The transcript's attachment
        // chips resolve the same way through the shared `MessagePartsView` owner.)
        const label = row.name || file?.title || file?.filename;
        if (!label) return null;
        const href = file?.id ? recordHref(FILE_MODEL, file.id) : undefined;
        return href ? (
          <TextLink href={href}>{label}</TextLink>
        ) : (
          <span className="text-fg-subtle">{label}</span>
        );
      },
    },
  ];
}

/** The message's structural content: its part rows as the shared nested data
 *  view — filter/sort/group chrome included — filtered to this record, grouped
 *  by role by default, regroupable by shared fragment for the dedup lens. */
function MessagePartsTab({ recordId }: RecordPanelContext): React.ReactElement {
  const t = useMessagingT();
  const recordHref = useResourceRecordHrefLookup();
  const columns = React.useMemo(() => partColumns(t, recordHref), [t, recordHref]);
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

/** Human-readable MIME body and attachments, rendered by the shared message owner. */
function MessageReadableBody({ recordId }: Pick<RecordPanelContext, "recordId">): React.ReactElement {
  const t = useMessagingT();
  const query = useAuthoredQuery(MessageDetailPartsDocument, { id: recordId }, {
    models: [MODEL, PART_MODEL, FILE_MODEL],
    records: [{ model: MODEL, id: recordId }],
  });
  if (query.isFetching && !query.data) return <LoadingPanel message={t("messages.loadingBody")} />;
  if (query.error && !query.data) return <ErrorBanner description={t("messages.bodyUnavailable")} />;
  const message = query.data?.messages[0];
  if (!message) return <ErrorBanner description={t("messages.bodyUnavailable")} />;
  return <MessagePartsView parts={message.parts} className="px-1" />;
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
        {/* The channel FK targets the Integration parent (a Channel or a posts
            Feed), so the vendor — not the channel's own backend_class — is the
            projected fact that names the platform for every row. */}
        <Column field="channel_vendor_name" header={t("messages.channelType")} />
        <Column field="status" widget="statusBadge" />
        <Column field="sent_at" />
      </List>
    </ResourceList>
  );
}

/** The canonical Message detail, reused by the inbox and passive record peeks. */
function MessageForm({ resource: _resource, readOnly, ...props }: RegisteredFormProps): React.ReactElement {
  const t = useMessagingT();
  const recordTabs = React.useMemo(() => messageRecordTabs(t), [t]);
  return <Form
    {...props}
    resource={MODEL}
    readOnly={readOnly}
    recordTabs={recordTabs}
    formExtras={({ recordId }) => recordId ? <MessageReadableBody recordId={recordId} /> : null}
    title={({ record }) => messageSubject(record?.title, t("messages.noSubject"))}
  >
    <Field name="title" title readOnly />
    <Field name="status" readOnly />
    <Group label={t("messages.groupEnvelope")} columns={2}>
      <Field name="sender" readOnly />
      <Field name="sent_at" readOnly />
      <Field name="platform" readOnly />
      <Field name="direction" readOnly />
      <Field name="external_id" readOnly />
    </Group>
    {!readOnly ? <>
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
    </> : null}
  </Form>;
}

export const messageForm = registerForm(MODEL, MessageForm);

function messageSubject(value: unknown, fallback: string): string {
  const subject = typeof value === "string" ? value.trim() : "";
  return subject || fallback;
}
