import { type ReactElement } from "react";

import { Field, FormView, Group } from "@angee/ui";

import { useStorageT } from "../i18n";
import type { StorageFile } from "../data/documents";

/** The Django model label backing the file record form. */
const FILE_MODEL = "storage.File";
// created/updated feed the FormView record subtitle (id · created · updated);
// they ride along in the record query but stay out of the field grid.
const SUBTITLE_FIELDS = ["created_at", "updated_at"] as const;

export interface FileDetailProps {
  file: StorageFile;
  /** A write landed — refetch the browser's shared file set. */
  onChanged: () => void;
  /** Render the detail fields for a narrow side pane. */
  compact?: boolean;
}

/**
 * One file as an editable metadata record: the title input renames it, and a
 * read-only detail group surfaces the stored filename, owner, and stage. The
 * file's lifecycle verbs (download, trash/restore) and the record pager live in
 * the page's control band, beside the content they act on; this is just the
 * metadata form the page publishes into the chatter's details tab.
 */
export function FileDetail({
  file,
  onChanged,
  compact = false,
}: FileDetailProps): ReactElement {
  const t = useStorageT();

  return (
    <FormView
      resource={FILE_MODEL}
      id={file.id}
      returning={[...SUBTITLE_FIELDS]}
      submitLabel={t("file.rename")}
      onSaved={onChanged}
    >
      <Field name="title" widget="text" title placeholder={file.filename} />
      <Group label={t("file.details")} columns={compact ? 1 : 2}>
        <Field name="filename" label={t("file.filename")} readOnly />
        <Field name="created_by_label" label={t("file.owner")} widget="userRef" readOnly />
        <Field name="upload_state" label={t("file.stage")} readOnly />
      </Group>
    </FormView>
  );
}
