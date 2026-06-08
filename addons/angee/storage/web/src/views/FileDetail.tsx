import { type ReactElement } from "react";

import { Button, Field, FormView, Glyph, Group, buttonVariants } from "@angee/base";
import { useResourceRecord } from "@angee/sdk";

import type { StorageFile } from "../data/documents";
import { useFileActions } from "../data/use-file-actions";

/** The Django model label backing the file record form and its crumb. */
const FILE_MODEL = "storage.File";
// created/updated feed the FormView record subtitle (id · created · updated);
// they ride along in the record query but stay out of the field grid.
const SUBTITLE_FIELDS = ["createdAt", "updatedAt"] as const;

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
  const actions = useFileActions({ onChanged });
  const canDownload = !file.isTrashed && file.url !== "";

  return (
    <FormView
      model={FILE_MODEL}
      id={file.id}
      returning={[...SUBTITLE_FIELDS]}
      submitLabel="Rename"
      onSaved={onChanged}
      toolbarStart={
        <Button type="button" size="sm" variant="ghost" onClick={onClose}>
          <Glyph name="chevron-left" />
          Files
        </Button>
      }
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
              Download
            </a>
          ) : null}
          {file.isTrashed ? (
            <Button
              type="button"
              size="sm"
              variant="secondary"
              loading={actions.busy}
              onClick={() => void actions.restore(file.id)}
            >
              <Glyph name="restore" />
              Restore
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
              Trash
            </Button>
          )}
        </>
      }
    >
      <Field name="title" widget="text" title placeholder={file.filename} />
      <Group label="Details" columns={2}>
        <Field name="filename" label="Filename" readOnly />
        <Field name="createdByLabel" label="Owner" widget="userRef" readOnly />
        <Field name="uploadState" label="Stage" readOnly />
      </Group>
    </FormView>
  );
}

/** The record crumb for `/storage/$id` — the file's title (or stored filename). */
export function FileCrumb({ id }: { id: string }): ReactElement {
  const { fetching, record } = useResourceRecord(FILE_MODEL, id || null, {
    enabled: id !== "",
    fields: ["title", "filename"],
  });
  const title = typeof record?.title === "string" ? record.title.trim() : "";
  const filename = typeof record?.filename === "string" ? record.filename : "";
  if (fetching) return <>…</>;
  return <>{title || filename || "File"}</>;
}
