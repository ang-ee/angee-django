import * as React from "react";
import { ResourceList, Form, List, Column, Field, Group, REFINE_CREATE_ID, Statusline, StatusSegment, StatuslineSpacer, useRouteHref, type ResourceViewDefaultGroups } from "@angee/ui";
import { useParams } from "@tanstack/react-router";

import { useNotesT, type NotesT } from "./i18n";

const MODEL = "notes.Note";

const NOTE_DEFAULT_GROUPS = {
  list: { field: "updated_at", granularity: "month" },
  board: { field: "status" },
} satisfies ResourceViewDefaultGroups;

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
      {/* tags is a JSON list: list cells render enums, relations and plain
          scalars natively, so without an explicit widget the cell falls through
          to the raw array (`["triage","backend"]`). tagInput draws the chips. */}
      <Column field="tags" widget="tagInput" sortable={false} />
      <Column field="status" widget="statusBadge" />
      <Column field="word_count" align="right" aggregate="sum" />
      <Column field="updated_at" />
    </List>
  );
}

function noteForm(t: NotesT): React.ReactElement {
  return (
    <Form resource={MODEL}>
      <Field name="title" widget="text" title />
      <Field name="status" widget="statusbar" status />
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
  return (
    <div className="flex flex-col gap-4">
      {/* Open as a month-grouped list; board view switches to status lanes. */}
      <ResourceList
        resource={MODEL}
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
        <StatuslineSpacer />
        <StatusSegment>notes.Note</StatusSegment>
        <StatusSegment icon="grid">console</StatusSegment>
      </Statusline>
    </div>
  );
}
