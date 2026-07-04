import * as React from "react";
import {
  ResourceList,
  Form,
  List,
  Column,
  Field,
  Group,
  Page,
  REFINE_CREATE_ID,
  RevisionsTab,
  Statusline,
  StatusSegment,
  StatuslineSpacer,
  useResourceRevisions,
  type ChatterTab,
  type ResourceViewDefaultGroups,
  type RecordSmartButtonDescriptor,
  useChatterContent,
} from "@angee/ui";
import { useParams } from "@tanstack/react-router";

const MODEL = "notes.Note";

const NOTE_DEFAULT_GROUPS = {
  list: { field: "updated_at", granularity: "month" },
  board: { field: "status" },
} satisfies ResourceViewDefaultGroups;

// Created/updated timestamps + word count feed the record subtitle (id · created
// · updated · words); they are queried but kept out of the field grid.
const RECORD_SUBTITLE_FIELDS: readonly string[] = [
  "created_at",
  "updated_at",
  "word_count",
];

const noteList = (
  <List
    resource={MODEL}
    defaultGroups={NOTE_DEFAULT_GROUPS}
    order={{ updated_at: "DESC" }}
    emptyContent={{
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
    <Column field="word_count" align="right" aggregate="sum" />
    <Column field="updated_at" />
  </List>
);

const noteForm = (
  <Form resource={MODEL} returning={RECORD_SUBTITLE_FIELDS}>
    <Field name="title" widget="text" title />
    <Field name="status" widget="statusbar" />
    <Group label="Details" columns={2}>
      <Field name="created_by_label" label="Owner" widget="userRef" readOnly />
      <Field name="reminder_at" label="Reminder" widget="datetime" />
      <Field name="tags" widget="tagInput" />
    </Group>
    <Field name="body" widget="markdown.editor" />
  </Form>
);

/** The notes console page: a count-by-status panel above the data table. */
export function NotePage(): React.ReactElement {
  // The nested record route (`notes.record`) carries no component; this parent
  // surface reads its `$id` param directly.
  const params = useParams({ strict: false });
  const routeId =
    "id" in params && typeof params.id === "string" ? params.id : undefined;
  const creating = routeId === REFINE_CREATE_ID;
  const recordId = creating ? null : routeId;
  const activeRecordId =
    !creating && typeof recordId === "string" ? recordId : null;
  const revisions = useResourceRevisions(MODEL, activeRecordId, {
    enabled: activeRecordId !== null,
  });
  const tabs = React.useMemo(
    () => [
      {
        id: "activity",
        label: "Activity",
        icon: "activity",
        count: revisions.count,
        children: <RevisionsTab resource={MODEL} recordId={activeRecordId} />,
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
    <Page height="auto" overflow="visible" className="min-h-full gap-4">
      {/* Open as a month-grouped list; board view switches to status lanes. */}
      <ResourceList
        resource={MODEL}
        recordSmartButtons={recordSmartButtons}
        placement="inline"
        routed
      >
        {noteList}
        {noteForm}
      </ResourceList>
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
    </Page>
  );
}
