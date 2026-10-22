---
"@angee/ui": major
"@angee/refine": major
"@angee/metadata": major
"@angee/app": major
---

Record revisions are retired with django-reversion. A record's changes are its
History tab, which `@angee/provenance` contributes at `form#sections` on every
form whose resource declares `history: true`; that package exports
`RecordHistoryTimeline`, `HistoryPane` and `useRecordHistory` for pages that embed
the same entries.

- `@angee/ui`: removed `RevisionsTab` and `RevisionsTabProps`; remove any tab
  that rendered it. Also removed:
  `useResourceRevisions` with its option and result types, the
  `@angee/ui/data/revisions` entry point, `useRevisionOperation`, and the
  all `revisions.*` messages. `TabProps.visibleWhen` and
  `RecordTabDescriptor.visibleWhen` receive the form's data resource as a second
  argument.
- `@angee/refine`: removed `useAngeeRevisions`, `UseAngeeRevisionsResult`,
  `revisionsRequest`, `extractRevisions`, `revisionSnapshot`, `ResourceRevision`,
  `revisionDocumentForResource`, `RevisionDocumentMap` and
  `SchemaOperationDocuments.revisions`.
- `@angee/metadata`: resource metadata no longer declares `roots.revisions` or
  `typeNames.revision`; `history` marks a resource that keeps history.
- `@angee/app`: codegen no longer writes `revisionDocuments`, `RevisionResource`,
  `RevisionDocument`, `RevisionRecord` or `RevisionVariables`.
