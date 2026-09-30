# Archive import workflows

`archive_import_file` and `archive_import_drive` share one graph. Registered
`ArchiveExtractor` implementations recognize a readable storage subject,
the archive review freezes their identities and collects target IDs, and the
workflow `map` runs one IO attempt per confirmed mapping. Extractors own their
vendor parsing and idempotent target ingest. Each attempt reports progress
through `ArchiveExecutionReporter.heartbeat()`; the workflow retains source
and target artifacts.

`ArchiveExtractor` owns the `ANGEE_WORKFLOW_ARCHIVE_EXTRACTOR_CLASSES` registry;
its addon declares that base through `ANGEE_IMPL_REGISTRIES`.
Implementations import `ArchiveExtractor` and `ArchiveExecutionReporter` from
`angee.workflows_integrate.archive_steps`. Safe ZIP helpers live in
`angee.storage.archives`. The framework's workflow and decision owners handle
review settlement, map fan-out, retries, and run results.
