# Notes example

Notes composes the shared record UI and its Context aside through public addon
contracts. Its permissions extensions declare `notes.Note` as a target of
`storage.FileAttachment` and `knowledge.RecordBinding`, so resource metadata
exposes both edges and Related offers Files and Pages.

Note writers can attach files and bind pages they can also write. Record-scoped
uploads inherit note read/write through the model's `file_attachments` reverse
relation and the visibility-filtered `storage/file` arm; attaching an existing
private or inherited file does not change its access. The shared groups own
Upload and Bind; the example adds no custom pane or mutation.

See the [storage](../../../../addons/angee/storage/README.md) and
[knowledge](../../../../addons/angee/knowledge/README.md) contracts.
