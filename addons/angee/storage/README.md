# Record files

Storage contributes a **Files** chatter tab on record routes. `record_files`
returns attachments only when the actor can read the record and each file. The
file keeps its own permissions; attaching it never grants file access.

To enable record-scoped uploads, declare a reverse `GenericRelation` on the
canonical, REBAC-typed record owner and contribute a matching read/write arm to
`storage/file`. The relation path must target that model, and the arm must filter
`visibility: record`. For example, if `example/record` owns the canonical row:

```python
file_attachments = GenericRelation("storage.FileAttachment", related_query_name="example_record")
```

```zed
definition storage/file {
    relation record: example/record // rebac:field={"path":"attachments__example_record","filters":{"visibility":"record"}}
    permission read = record->read
    permission write = record->write
}
```

The model owner decides record read/write permissions. Storage checks the
effective arm before accepting a record upload, and the tab uses the existing
`file_upload_begin` record target with `visibility: RECORD`. Record routes can
admit `files` through their chatter tab policy when they opt into the aside.
