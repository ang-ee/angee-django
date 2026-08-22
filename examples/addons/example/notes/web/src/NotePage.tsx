import * as React from "react";
import { ResourceList, Form, List, Column, Field, Group, REFINE_CREATE_ID, RevisionsTab, Statusline, StatusSegment, StatuslineSpacer, useResourceRevisions, useRouteHref, type ChatterTab, type ResourceViewDefaultGroups, type RecordSmartButtonDescriptor, useChatterContent } from "@angee/ui";
import { useParams } from "@tanstack/react-router";

import { useNotesT, type NotesT } from "./i18n";

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

function noteList(agentsHref: string | undefined, t: NotesT): React.ReactElement {
  return (
    <List
      resource={MODEL}
      defaultGroups={NOTE_DEFAULT_GROUPS}
      order={{ updated_at: "DESC" }}
      emptyContent={{
        icon: "agent",
        title: t("empty.title"),
        description: t("empty.description"),
        ...(agentsHref
          ? {
              action: {
                label: t("empty.setupAssistant"),
                href: agentsHref,
                icon: "agent",
              },
            }
          : {}),
      }}
    >
      <Column field="title" />
      <Column field="tags" sortable={false} />
      <Column field="status" widget="statusBadge" />
      <Column field="word_count" align="right" aggregate="sum" />
      <Column field="updated_at" />
    </List>
  );
}

function noteForm(t: NotesT): React.ReactElement {
  return (
    <Form resource={MODEL} returning={RECORD_SUBTITLE_FIELDS}>
      <Field name="title" widget="text" title />
      <Field name="status" widget="statusbar" />
      <Group label={t("form.details")} columns={2}>
        <Field
          name="created_by_label"
          label={t("form.owner")}
          widget="userRef"
          readOnly
        />
        <Field name="reminder_at" label={t("form.reminder")} widget="datetime" />
        <Field name="tags" widget="tagInput" />
      </Group>
      <Field name="body" widget="markdown.editor" />
    </Form>
  );
}

/** The notes console page: a count-by-status panel above the data table. */
export function NotePage(): React.ReactElement {
  const t = useNotesT();
  const routeHref = useRouteHref();
  const agentsHref = routeHref.maybe("agents.agents");
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
        label: t("record.activity"),
        icon: "activity",
        count: revisions.count,
        children: <RevisionsTab resource={MODEL} recordId={activeRecordId} />,
      },
    ] satisfies readonly ChatterTab[],
    [activeRecordId, revisions.count, t],
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
          label: t("record.versions"),
        },
      ] satisfies readonly RecordSmartButtonDescriptor[],
    [revisions.count, t],
  );

  return (
    <div className="flex flex-col gap-4">
      {/* Open as a month-grouped list; board view switches to status lanes. */}
      <ResourceList
        resource={MODEL}
        recordSmartButtons={recordSmartButtons}
        placement="inline"
        routed
      >
        {noteList(agentsHref, t)}
        {noteForm(t)}
      </ResourceList>
      <Statusline>
        <StatusSegment icon="check" tone="success">
          {t("status.synced")}
        </StatusSegment>
        <StatusSegment icon="notes">
          {creating
            ? t("status.new")
            : activeRecordId
              ? t("status.editing")
              : t("status.all")}
        </StatusSegment>
        {activeRecordId ? (
          <StatusSegment icon="versions">
            {t("status.revision", { count: revisions.count })}
          </StatusSegment>
        ) : null}
        <StatuslineSpacer />
        <StatusSegment>notes.Note</StatusSegment>
        <StatusSegment icon="grid">console</StatusSegment>
      </Statusline>
    </div>
  );
}
