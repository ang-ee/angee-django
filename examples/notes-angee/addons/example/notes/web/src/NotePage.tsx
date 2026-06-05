import * as React from "react";
import {
  DataPage,
  Glyph,
  GroupListView,
  Spinner,
  type FormField,
  type ListColumn,
  type PageGroupDescriptor,
  useChatterContent,
} from "@angee/base";
import { useAuthoredQuery } from "@angee/sdk";
import { useNavigate, useParams } from "@tanstack/react-router";
import { formatDistanceToNow } from "date-fns";

import { NOTE_STATUS_OPTIONS, NOTE_STATUS_TONES } from "./note-status";

const MODEL = "notes.Note";
const NOTE_LIST_PATH = "/notes";
const NOTE_REVISIONS_QUERY = `
  query NoteRevisions($id: ID!) {
    noteRevisions(id: $id) {
      id
      createdAt
      comment
      body
    }
  }
`;

interface NoteRevision {
  id: string;
  createdAt: string;
  comment: string | null;
  body: string;
}

interface NoteRevisionsData {
  noteRevisions: NoteRevision[];
}

type NoteRevisionsVariables = Record<string, unknown> & {
  id: string;
};

interface NoteRouteParams {
  id?: string;
}

interface NotePageProps {
  routeRecordId?: string;
}

const columns: readonly ListColumn[] = [
  { field: "title", header: "Title" },
  { field: "tags", header: "Tags", sortable: false },
  { field: "status", header: "Status", tone: NOTE_STATUS_TONES },
  { field: "wordCount", header: "Word Count", align: "right", aggregate: "sum" },
  { field: "updatedAt", header: "Updated At" },
];

const titleField = {
  name: "title",
  label: "Title",
  widget: "text",
  title: true,
} satisfies FormField;
const statusField = {
  name: "status",
  label: "Status",
  widget: "statusbar",
  options: NOTE_STATUS_OPTIONS,
} satisfies FormField;
const tagsField = {
  name: "tags",
  label: "Tags",
  widget: "tagInput",
} satisfies FormField;
const reminderField = {
  name: "reminderAt",
  label: "Reminder",
  widget: "datetime",
} satisfies FormField;
const ownerField = {
  name: "createdByLabel",
  label: "Owner",
  widget: "userRef",
  readOnly: true,
} satisfies FormField;
const bodyField = {
  name: "body",
  label: "Body",
  widget: "markdown.editor",
} satisfies FormField;
const formFields: readonly FormField[] = [
  titleField,
  statusField,
  tagsField,
  reminderField,
  ownerField,
  bodyField,
];

// Created/updated timestamps + word count feed the record subtitle (id · created
// · updated · words); they are queried but kept out of the field grid.
const RECORD_SUBTITLE_FIELDS: readonly string[] = [
  "createdAt",
  "updatedAt",
  "wordCount",
];

const formGroups: readonly PageGroupDescriptor[] = [
  {
    label: "Details",
    columns: 2,
    fields: [ownerField, reminderField, tagsField],
    actions: [],
  },
  {
    label: "Body",
    fields: [bodyField],
    actions: [],
  },
];

export function NoteRecordPage(): React.ReactElement {
  const params = useParams({ strict: false }) as Partial<NoteRouteParams>;
  return <NotePage routeRecordId={routeRecordId(params.id)} />;
}

/** The notes console page: a count-by-status panel above the data table. */
export function NotePage({
  routeRecordId,
}: NotePageProps = {}): React.ReactElement {
  const navigate = useNavigate();
  const [recordId, setRecordId] = React.useState<string | null | undefined>(
    routeRecordId,
  );
  const [creating, setCreating] = React.useState(false);
  React.useEffect(() => {
    setCreating(false);
    setRecordId(routeRecordId);
  }, [routeRecordId]);
  const handleSelect = React.useCallback(
    (id: string | null) => {
      setCreating(id === null);
      setRecordId(id);
      if (typeof id === "string") {
        void navigate({ to: noteRecordPath(id) });
      }
    },
    [navigate],
  );
  const handleClose = React.useCallback(() => {
    setCreating(false);
    setRecordId(undefined);
    void navigate({ to: NOTE_LIST_PATH });
  }, [navigate]);

  return (
    <div className="flex flex-col gap-4">
      <NoteChatter recordId={recordId} creating={creating} />
      {/* Open flat, most-recent-first (order by updatedAt). A day-granularity
          default group is unusable against the seed's multi-year span — one
          folded group per day — so grouping is left to the toolbar control. */}
      <DataPage
        model={MODEL}
        columns={columns}
        formFields={formFields}
        formGroups={formGroups}
        returning={RECORD_SUBTITLE_FIELDS}
        recordId={recordId}
        creating={creating}
        placement="inline"
        list={GroupListView}
        pageSize={50}
        order={{ updatedAt: "DESC" }}
        rowHref={(row) =>
          typeof row.id === "string" ? noteRecordPath(row.id) : NOTE_LIST_PATH}
        onSelect={handleSelect}
        onClose={handleClose}
      />
    </div>
  );
}

function noteRecordPath(id: string): string {
  return `${NOTE_LIST_PATH}/${encodeURIComponent(id)}`;
}

function routeRecordId(id: string | undefined): string | undefined {
  if (id === undefined) return undefined;
  try {
    return decodeURIComponent(id);
  } catch {
    return id;
  }
}

function NoteChatter({
  recordId,
  creating,
}: {
  recordId: string | null | undefined;
  creating: boolean;
}): null {
  const activeRecordId =
    !creating && typeof recordId === "string" ? recordId : null;
  const revisions = useAuthoredQuery<NoteRevisionsData, NoteRevisionsVariables>(
    NOTE_REVISIONS_QUERY,
    { id: activeRecordId ?? "" },
    { enabled: activeRecordId !== null },
  );
  const tabs = React.useMemo(() => {
    const revisionCount = revisions.data?.noteRevisions.length;
    return [
      {
        id: "angee",
        label: "Angee",
        icon: "agent",
        children: (
          <RailEmptyState
            icon="agent"
            title="No agent yet"
            body="Set up your assistant"
          />
        ),
      },
      {
        id: "comments",
        label: "Comments",
        icon: "comments",
        children: (
          <RailEmptyState
            icon="comments"
            title="No comments yet"
            body="Comments will appear here."
          />
        ),
      },
      {
        id: "activity",
        label: "Activity",
        icon: "activity",
        ...(revisionCount !== undefined ? { count: revisionCount } : {}),
        children: (
          <NoteActivityPanel
            activeRecordId={activeRecordId}
            revisions={revisions.data?.noteRevisions ?? []}
            fetching={revisions.fetching}
            error={revisions.error}
          />
        ),
      },
    ];
  }, [
    activeRecordId,
    revisions.data?.noteRevisions,
    revisions.error,
    revisions.fetching,
  ]);
  const content = React.useMemo(() => ({ tabs }), [tabs]);
  useChatterContent(content);
  return null;
}

function NoteActivityPanel({
  activeRecordId,
  revisions,
  fetching,
  error,
}: {
  activeRecordId: string | null;
  revisions: readonly NoteRevision[];
  fetching: boolean;
  error: Error | null;
}): React.ReactElement {
  if (!activeRecordId) {
    return (
      <RailEmptyState
        icon="activity"
        title="No record selected"
        body="Open a note to view activity."
      />
    );
  }
  if (error) {
    return (
      <div role="alert" className="text-13 text-danger-text">
        {error.message}
      </div>
    );
  }
  if (fetching) {
    return (
      <div className="flex items-center gap-2 text-13 text-fg-muted">
        <Spinner size="sm" />
        Loading activity...
      </div>
    );
  }
  if (revisions.length === 0) {
    return (
      <RailEmptyState
        icon="activity"
        title="No revisions yet"
        body="Body changes will appear here."
      />
    );
  }
  return (
    <ol className="flex flex-col gap-3">
      {revisions.map((revision) => (
        <li
          key={revision.id}
          className="rounded-md border border-border-subtle bg-sheet-2 p-3"
        >
          <div className="flex min-w-0 items-start justify-between gap-2">
            <p className="truncate text-13 font-semibold text-fg">
              {revision.comment ?? "Body updated"}
            </p>
            <time
              dateTime={revision.createdAt}
              className="shrink-0 text-2xs tabular-nums text-fg-muted"
            >
              {relativeTime(revision.createdAt)}
            </time>
          </div>
          <p className="mt-2 line-clamp-3 text-13 leading-5 text-fg-2">
            {excerpt(revision.body)}
          </p>
        </li>
      ))}
    </ol>
  );
}

function RailEmptyState({
  icon,
  title,
  body,
}: {
  icon: string;
  title: React.ReactNode;
  body: React.ReactNode;
}): React.ReactElement {
  return (
    <div className="grid min-h-48 place-content-center gap-2 text-center">
      <div className="mx-auto grid size-10 place-content-center rounded-md bg-accent-soft text-accent-soft-text [&_.glyph]:size-5">
        <Glyph name={icon} />
      </div>
      <p className="text-sm font-semibold text-fg">{title}</p>
      <p className="text-13 text-fg-muted">{body}</p>
    </div>
  );
}

function relativeTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return formatDistanceToNow(date, { addSuffix: true });
}

function excerpt(value: string): string {
  const text = value.replace(/\s+/g, " ").trim();
  if (!text) return "No body snapshot.";
  return text.length > 160 ? `${text.slice(0, 157)}...` : text;
}
