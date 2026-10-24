# Manual operator backups

`BackupPlan` stores version-one selection intent for the local operator. Plans
are administered explicitly; `runner` grants permit preview and manual execution
without granting general operator access. Ownership alone grants no capture
capability. Plans select discovered names per database, service state, workspace,
source, and storage group through `all`, `selected`, or `none`, followed by
exclusions. Source policy defaults to portable `bundle`; `fetch_published` must
be supported and proven safe by the operator or discovery reports it unsupported.

The operator owns discovery, source size estimates, capture, and its durable
`backups/archives` directory. Source estimates are not compressed archive size
predictions. Unknown sizes, exclusions, and unsupported resources remain visible.
Databases are configured connections, never the PostgreSQL cluster or raw pgdata.
Only stack-owned data is eligible for `all`.

Configure an existing `storage_integrate.Mount` in **reference** mode with the
`local_folder` backend rooted at the operator's `backups/archives` publication
directory. Set `ANGEE_OPERATOR_BACKUP_MOUNT_DRIVE` to that mount's drive slug
(default `mount-operator-backups`). The normal Mount sync can index existing archives,
including after application database loss. The Files backend serves downloads;
no operator credentials or source filesystem paths reach the console preview.

`Back up now` starts the installed `operator_backup_capture` workflow with a
frozen plan input and caller request key. Its IO step idempotently dispatches
and polls the native operator operation. The operator verifies ZIP entries and
hashes the archive before atomic publication; Django trusts that authenticated
receipt and checks the local regular file's size and configured destination.
The reference Mount must match the authenticated operator stack root's
`backups/archives` directory; its drive backend, root and empty prefix must agree
with the native Mount backend. Publication refuses destination configuration
drift after capture. Its database step indexes the archive as a storage File
and publishes one immutable `Backup` per execution with the actual frozen
operator manifest. File alone owns filename, content hash, byte count, and
download authorization. Native workflow links and timeline retain progress and
failed attempts; unsuccessful capture or publication creates no completed Backup.
A terminal operator capture failure requires the workflow's **Reprocess** action:
that preserves its frozen selection and creates a new capture identity.
Automatic transport retries resume the same operator identity up to the capture
step's declared bound. Request validation, missing endpoint and access refusals
fail immediately with safe configuration or selection guidance; temporary
availability and busy responses use that bounded retry policy. Once capture
execution fails, manual recovery uses Reprocess. An exhausted
task-delivery wait can use native Retry to resume the same operation identity.
A failed publication can use native in-place Retry after its destination is
repaired, preserving the completed archive. The singleton
`backup_destination_drive` query
resolves the configured Files location once rather than once per listed plan.

Archives and completed evidence are retained. Schedules and cold restore
execution are intentionally deferred; the versioned manifest retains recovery
instructions and source revision evidence for future restore tooling.
