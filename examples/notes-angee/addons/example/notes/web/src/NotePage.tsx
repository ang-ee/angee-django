import * as React from "react";
import {
  DataPage,
  EmptyState,
  Form,
  GroupListView,
  List,
  Column,
  Field,
  Group,
  NEW_RECORD_ID,
  RevisionsTab,
  Statusline,
  StatusSegment,
  StatuslineSpacer,
  type ChatterTab,
  type DataViewDefaultGroups,
  type RecordSmartButtonDescriptor,
  useChatterContent,
} from "@angee/base";
import { AgentChatterPane } from "@angee/agents";
import { useModelMetadata, useResourceRecord, useResourceRevisions } from "@angee/sdk";
import { useParams } from "@tanstack/react-router";

const MODEL = "notes.Note";
// The rebac resource type the agent's view envelope + notes MCP tools key on.
const NOTE_RESOURCE_TYPE = "notes/note";

const NOTE_DEFAULT_GROUPS = {
  list: { field: "updatedAt", granularity: "month" },
  board: { field: "status" },
} satisfies DataViewDefaultGroups;

// Created/updated timestamps + word count feed the record subtitle (id · created
// · updated · words); they are queried but kept out of the field grid.
const RECORD_SUBTITLE_FIELDS: readonly string[] = [
  "createdAt",
  "updatedAt",
  "wordCount",
];

const noteList = (
  <List
    model={MODEL}
    list={GroupListView}
    defaultGroups={NOTE_DEFAULT_GROUPS}
    order={{ updatedAt: "DESC" }}
    emptyState={{
      icon: "agent",
      title: "No notes yet",
      description: "The agent isn't running yet — provision it to start chatting.",
      action: {
        label: "Set up your assistant",
        href: "/agents",
        icon: "agent",
      },
    }}
  >
    <Column field="title" />
    <Column field="tags" sortable={false} />
    <Column field="status" widget="statusBadge" />
    <Column field="wordCount" align="right" aggregate="sum" />
    <Column field="updatedAt" />
  </List>
);

const noteForm = (
  <Form model={MODEL} returning={RECORD_SUBTITLE_FIELDS}>
    <Field name="title" widget="text" title />
    <Field name="status" widget="statusbar" />
    <Group label="Details" columns={2}>
      <Field name="createdByLabel" label="Owner" widget="userRef" readOnly />
      <Field name="reminderAt" label="Reminder" widget="datetime" />
      <Field name="tags" widget="tagInput" />
    </Group>
    <Field name="body" widget="markdown.editor" />
  </Form>
);

/** The record crumb for `/notes/$id` — resolves the note title from the cache. */
export function NoteCrumb({ id }: { id: string }): React.ReactElement {
  const isNew = id === NEW_RECORD_ID;
  const metadata = useModelMetadata(MODEL);
  const representationField = metadata?.recordRepresentation ?? "title";
  const { fetching, record } = useResourceRecord(MODEL, isNew ? null : id, {
    enabled: !isNew && id !== "",
    fields: [representationField],
  });
  const value = record?.[representationField];
  const title = typeof value === "string" ? value.trim() : "";
  if (isNew) return <>New</>;
  if (fetching) return <>…</>;
  return <>{title || "Note"}</>;
}

/** The notes console page: a count-by-status panel above the data table. */
export function NotePage(): React.ReactElement {
  // The nested record route (`notes.record`) carries no component; this parent
  // surface reads its `$id` param directly.
  const params = useParams({ strict: false });
  const routeId =
    "id" in params && typeof params.id === "string" ? params.id : undefined;
  const creating = routeId === NEW_RECORD_ID;
  const recordId = creating ? null : routeId;
  const activeRecordId =
    !creating && typeof recordId === "string" ? recordId : null;
  const revisions = useResourceRevisions(MODEL, activeRecordId, {
    enabled: activeRecordId !== null,
  });
  const tabs = React.useMemo(
    () => [
      {
        id: "angee",
        label: "Angee",
        icon: "agent",
        children: (
          <AgentChatterPane
            model={NOTE_RESOURCE_TYPE}
            recordId={activeRecordId ?? undefined}
          />
        ),
      },
      {
        id: "comments",
        label: "Comments",
        icon: "comments",
        children: (
          <EmptyState
            icon="comments"
            title="No comments yet"
            description="Comments will appear here."
            className="min-h-48 p-4"
          />
        ),
      },
      {
        id: "activity",
        label: "Activity",
        icon: "activity",
        count: revisions.count,
        children: <RevisionsTab model={MODEL} recordId={activeRecordId} />,
      },
    ] satisfies readonly ChatterTab[],
    [activeRecordId, revisions.count],
  );
  const chatter = React.useMemo(() => ({ tabs }), [tabs]);
  useChatterContent(chatter);
  const recordSmartButtons = React.useMemo(
    () =>
      [
        {
          id: "versions",
          icon: "versions",
          count: revisions.count,
          label: "Versions",
        },
      ] satisfies readonly RecordSmartButtonDescriptor[],
    [revisions.count],
  );

  return (
    <div className="flex flex-col gap-4">
      {/* Open as a month-grouped list; board view switches to status lanes. */}
      <DataPage
        model={MODEL}
        recordSmartButtons={recordSmartButtons}
        placement="inline"
        routed
      >
        {noteList}
        {noteForm}
      </DataPage>
      <Statusline>
        <StatusSegment icon="check" tone="success">
          Synced
        </StatusSegment>
        <StatusSegment icon="notes">
          {creating
            ? "New note"
            : activeRecordId
              ? "Editing note"
              : "All notes"}
        </StatusSegment>
        {activeRecordId ? (
          <StatusSegment icon="versions">
            {revisions.count} {revisions.count === 1 ? "revision" : "revisions"}
          </StatusSegment>
        ) : null}
        <StatuslineSpacer />
        <StatusSegment>notes.Note</StatusSegment>
        <StatusSegment icon="grid">console</StatusSegment>
      </Statusline>
    </div>
  );
}
