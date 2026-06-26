import { type ReactElement } from "react";

import { Button, Field, FormView, Glyph, Group, buttonVariants } from "@angee/ui";

import { useStorageT } from "../i18n";
import type { StorageFile } from "../data/documents";
import { useFileActions } from "../data/use-file-actions";

/** The Django model label backing the file record form. */
const FILE_MODEL = "storage.File";
// created/updated feed the FormView record subtitle (id · created · updated);
// they ride along in the record query but stay out of the field grid.
const SUBTITLE_FIELDS = ["created_at", "updated_at"] as const;

export interface FileDetailProps {
  file: StorageFile;
  /** Leave the detail for the file list (the record route closed). */
  onClose: () => void;
  /** A write landed — refetch the browser's shared file set. */
  onChanged: () => void;
}

/**
 * One file as an editable record: the title input renames it, the toolbar
 * carries the download and trash/restore actions, and a read-only detail group
 * surfaces the stored filename, owner, and stage. The larger preview sits in the
 * Explorer aside beside this form.
 */
export function FileDetail({
  file,
  onClose,
  onChanged,
}: FileDetailProps): ReactElement {
  const t = useStorageT();
  const actions = useFileActions({ onChanged });
  const canDownload = !file.is_trashed && file.url !== "";

  return (
    <FormView
      resource={FILE_MODEL}
      id={file.id}
      returning={[...SUBTITLE_FIELDS]}
      submitLabel={t("storage.file.rename")}
      onSaved={onChanged}
      toolbar={
        <>
          {canDownload ? (
            // A real download anchor (the token URL is same-origin), styled as a
            // button — `Button asChild` would force a button role onto the link.
            <a
              className={buttonVariants({ variant: "secondary", size: "sm" })}
              href={file.url}
              download={file.filename}
            >
              <Glyph name="download" />
              {t("storage.file.download")}
            </a>
          ) : null}
          {file.is_trashed ? (
            <Button
              type="button"
              size="sm"
              variant="secondary"
              loading={actions.busy}
              onClick={() => void actions.restore(file.id)}
            >
              <Glyph name="restore" />
              {t("storage.file.restore")}
            </Button>
          ) : (
            <Button
              type="button"
              size="sm"
              variant="ghost"
              loading={actions.busy}
              onClick={() => void actions.trash(file.id).then(onClose)}
            >
              <Glyph name="trash" />
              {t("storage.file.trash")}
            </Button>
          )}
        </>
      }
    >
      <Field name="title" widget="text" title placeholder={file.filename} />
      <Group label={t("storage.file.details")} columns={2}>
        <Field name="filename" label={t("storage.file.filename")} readOnly />
        <Field name="created_by_label" label={t("storage.file.owner")} widget="userRef" readOnly />
        <Field name="upload_state" label={t("storage.file.stage")} readOnly />
      </Group>
    </FormView>
  );
}
