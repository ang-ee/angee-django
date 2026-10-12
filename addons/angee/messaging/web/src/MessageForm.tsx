import * as React from "react";
import {
  Action, Field, Form, Group, ListView, LoadingPanel, ErrorBanner,
  NavLink, registerForm, useResourceRecordHrefLookup,
  type ListColumn, type RecordPanelContext, type RecordTabDescriptor,
  type RegisteredFormProps, useTrashActions,
  useResolvedWidget,
} from "@angee/ui";
import { useModelMetadata } from "@angee/metadata";
import { useAuthoredQuery } from "@angee/refine";

import { useMessagingT } from "./i18n";
import { MessageReaderDocument, type PartListRow } from "./documents";
import { MessageReader } from "./MessageReader";

const MODEL = "messaging.Message";
const PART_MODEL = "messaging.Part";
// A part's attachment is a storage.File; its routed record page (breadcrumbs
// included) is the follow target for the attachment cell.
const FILE_MODEL = "storage.File";
const STAR_MODEL = "messaging.MessageStar";
const MESSAGE_TAG_FIELDS = ["tags.id", "tags.name", "permissions"];

// The structural tab defaults to grouping the part rows by role (title / header /
// body / quoted / signature); regrouping by fragment.hash through the shared
// grouping chooser turns the same view into the dedup/interconnection lens.
const PART_GROUPS = { list: { field: "role" } } as const;
// The Content pane names the list, so it carries no heading of its own.
const PART_CHROME = { search: true, heading: false } as const;

type PartRow = PartListRow;
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
          <NavLink href={href} variant="inline">{label}</NavLink>
        ) : (
          <span className="text-fg-subtle">{label}</span>
        );
      },
    },
  ];
}

/** The message's structural content: its part rows as a compact embedded list
 *  filtered to this record, grouped by role by default. Its search row stays on
 *  so the grouping chooser can regroup by shared fragment for the dedup lens. */
function MessagePartsTab({ recordId }: RecordPanelContext): React.ReactElement {
  const t = useMessagingT();
  const recordHref = useResourceRecordHrefLookup();
  const columns = React.useMemo(() => partColumns(t, recordHref), [t, recordHref]);
  return (
    <ListView<PartRow>
      resource={PART_MODEL}
      presentation="embedded"
      chrome={PART_CHROME}
      fields={PART_FIELDS}
      baseFilter={{ message: { exact: recordId } }}
      columns={columns}
      defaultGroups={PART_GROUPS}
      emptyContent={t("parts.empty")}
    />
  );
}

/** The message read like an email; an attachment it opens shows beside the body. */
function MessageRecordReader({ recordId }: Pick<RecordPanelContext, "recordId">): React.ReactElement {
  const t = useMessagingT();
  const [previewPartId, setPreviewPartId] = React.useState<string | null>(null);
  const query = useAuthoredQuery(MessageReaderDocument, { id: recordId }, {
    models: [MODEL, PART_MODEL, FILE_MODEL, STAR_MODEL],
    records: [{ model: MODEL, id: recordId }],
  });
  if (query.isFetching && !query.data) return <LoadingPanel message={t("messages.loadingBody")} />;
  const message = query.data?.messages[0];
  if (!message) return <ErrorBanner description={t("messages.bodyUnavailable")} />;
  return <MessageReader message={message} previewPartId={previewPartId} onPreviewPartChange={setPreviewPartId} />;
}

export function messageRecordTabs(
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

/** The canonical Message detail, reused by Messages and passive record peeks: the
 *  reader above the Content and Envelope tabs. */
function MessageForm({ resource: _resource, readOnly, ...props }: RegisteredFormProps): React.ReactElement {
  const t = useMessagingT();
  const recordTabs = React.useMemo(() => messageRecordTabs(t), [t]);
  const RecordTags = useResolvedWidget("angee.tags.recordTags")?.edit;
  // Moderation is the shared trash: channel managers move a message there and
  // back, offered by the message's projected `delete` permission.
  const trashActions = useTrashActions(useModelMetadata(MODEL)?.resource);
  const returning = React.useMemo(() => [...(props.returning ?? []), ...MESSAGE_TAG_FIELDS], [props.returning]);
  return <Form
    {...props}
    resource={MODEL}
    readOnly={readOnly}
    returning={returning}
    recordTabs={recordTabs}
    formExtras={({ recordId }) => recordId ? <>
      {RecordTags ? <RecordTags readOnly={readOnly} /> : null}
      <MessageRecordReader recordId={recordId} />
    </> : null}
    title={({ record }) => messageSubject(record?.title, t("messages.noSubject"))}
  >
    <Field name="title" title readOnly />
    <Field name="status" readOnly />
    {/* The reader shows the sender and date; the envelope keeps the transport facts. */}
    <Group label={t("messages.groupEnvelope")} columns={2} pane="envelope">
      <Field name="platform" readOnly />
      <Field name="direction" readOnly />
      <Field name="external_id" readOnly />
    </Group>
    <Field name="is_trashed" hidden readOnly />
    {!readOnly ? trashActions.map((action) => <Action key={action.id} {...action} />) : null}
  </Form>;
}

export const messageForm = registerForm(MODEL, MessageForm);

export function messageSubject(value: unknown, fallback: string): string {
  const subject = typeof value === "string" ? value.trim() : "";
  return subject || fallback;
}
