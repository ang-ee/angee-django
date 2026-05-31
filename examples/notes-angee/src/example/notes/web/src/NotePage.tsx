import * as React from "react";
import {
  AggregatePanel,
  DataPage,
  type FormField,
  type ListColumn,
  type PageGroupDescriptor,
} from "@angee/base";

import { NOTE_STATUS_OPTIONS, NOTE_STATUS_TONES } from "./note-status";

const MODEL = "notes.Note";

const columns: readonly ListColumn[] = [
  { field: "title", header: "Title" },
  { field: "tags", header: "Tags" },
  { field: "status", header: "Status", tone: NOTE_STATUS_TONES },
  { field: "wordCount", header: "Words", align: "right" },
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
const wordCountField = {
  name: "wordCount",
  label: "Word Count",
  readOnly: true,
} satisfies FormField;
const createdByField = {
  name: "createdBy",
  label: "Created By",
  widget: "userRef",
  readOnly: true,
} satisfies FormField;
const updatedByField = {
  name: "updatedBy",
  label: "Updated By",
  widget: "userRef",
  readOnly: true,
} satisfies FormField;
const createdAtField = {
  name: "createdAt",
  label: "Created At",
  widget: "datetime",
  readOnly: true,
} satisfies FormField;
const updatedAtField = {
  name: "updatedAt",
  label: "Updated At",
  widget: "datetime",
  readOnly: true,
} satisfies FormField;
const bodyField = {
  name: "body",
  label: "Body",
  widget: "markdown.editor",
} satisfies FormField;
const isStarredField = {
  name: "isStarred",
  label: "Starred",
  widget: "switch",
} satisfies FormField;

const formFields: readonly FormField[] = [
  titleField,
  statusField,
  tagsField,
  wordCountField,
  createdByField,
  updatedByField,
  createdAtField,
  updatedAtField,
  bodyField,
  isStarredField,
];

const formGroups: readonly PageGroupDescriptor[] = [
  {
    label: "Details",
    columns: 2,
    fields: [
      titleField,
      tagsField,
      isStarredField,
      wordCountField,
      createdByField,
      updatedByField,
      createdAtField,
      updatedAtField,
    ],
    actions: [],
  },
  {
    label: "Body",
    fields: [bodyField],
    actions: [],
  },
];

/** The notes console page: a count-by-status panel above the data table. */
export function NotePage(): React.ReactElement {
  const [recordId, setRecordId] = React.useState<string | null | undefined>(
    undefined,
  );
  const [creating, setCreating] = React.useState(false);

  return (
    <div className="flex flex-col gap-4">
      <AggregatePanel
        model={MODEL}
        dimensions={[{ field: "STATUS", key: "status", label: "By status" }]}
        title="Notes by status"
      />
      <DataPage
        model={MODEL}
        columns={columns}
        formFields={formFields}
        formGroups={formGroups}
        recordId={recordId}
        creating={creating}
        placement="drawer"
        pageSize={50}
        defaultGroup={{ field: "updatedAt", granularity: "day" }}
        onSelect={(id) => {
          setCreating(id === null);
          setRecordId(id);
        }}
        onClose={() => {
          setCreating(false);
          setRecordId(undefined);
        }}
      />
    </div>
  );
}
