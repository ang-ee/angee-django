# Record files

Storage contributes a **Files** group in Related on the record routes of types that
accept attachments: those whose resource metadata lists `storage.FileAttachment`
in `recordEdges`. `record_files` returns attachments only when the actor can read
the record and each file. The file keeps its own permissions; attaching it never
grants file access.

A record type accepts attachments once its owner declares it on
`storage/file_attachment` from its `permissions.extends.zed`: one relation backed
by the edge's `target`, unioned into `target_write`, so attaching requires write
on the record and an undeclared type is refused. To enable record-scoped uploads
as well, declare a reverse `GenericRelation` on the canonical, REBAC-typed record
owner and contribute a matching read/write arm to `storage/file`. The relation
path must target that model, and the arm must filter `visibility: record`. For
example, if `example/record` owns the canonical row:

```python
file_attachments = GenericRelation("storage.FileAttachment", related_query_name="example_record")
```

```zed
definition storage/file_attachment {
    relation record: example/record // rebac:field=target
    permission target_write = record->write
}

definition storage/file {
    relation record: example/record // rebac:field={"path":"attachments__example_record","filters":{"visibility":"record"}}
    permission read = record->read
    permission write = record->write
}
```

The model owner decides record read/write permissions. Storage checks the
effective arm before accepting a record upload, `record_files` offers uploads
(`can_upload`) only where it exists and the actor can write the record, and the
group uses the existing `file_upload_begin` record target with `visibility: RECORD`. The group is the
`related#groups/storage.files` child of the framework's Related tab; a product
narrowing `record#aside` with `only` keeps it by listing `related`.

Upload lives on the group label through `render(context, label)` and
`PaneLabelPopover`. The group component owns one `useStorageUpload` instance
shared with the row drop target. The picker shows a drive select only when there
is a choice, plus the labelled Choose files input. Progress and retries stay below
the rows when the popover closes. `useVerb` includes the actor's upload permission
and view-as restrictions. File rows use `PaneRow` links to the file's record page.

The file page contributes `storage.File#groups/storage.attached-to`. Forward and
reverse reads share `FileAttachmentType`; the reverse read checks file access and
uses the shared record-reference projection to redact
unreadable targets. `RecordReferenceRow` resolves metadata-owned labels in one
bounded cached query per model and follows registered record routes; targets
without a route remain non-interactive.

`StoragePage` owns URL navigation and composes `StorageExplorer` (the shared
`ScopedExplorerPane` and lazy folder reads), `StorageListContent` (list scope,
uploads and bulk actions), and `FilePreviewFrame` (preview, inline Details and
the shell's Download/Trash control band, reading its own file). Upload state survives list/record
navigation, and the shared explorer keeps its mounted tree and selection.

Folders compose the native hierarchy and transferable ownership, scoped to their
drive. Files, folders and FileAttachment edges retain changeset history; a merge
can restore an attachment's former target without moving the file's bytes.


GraphQL byte counts use the locked aggregation library's native `BigInt` scalar:
exact integer strings on input and output, including file sizes above 2 GiB.
The shared size formatter converts to an approximate number only for human
readable display. Persistence remains Django's positive bigint column.
