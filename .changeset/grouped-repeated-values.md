---
"@angee/ui": minor
"@angee/metadata": minor
---

A grouping level can show only repeated values: `repeated` on a `GroupSpec`
("Repeated values only", `?group=<field>:repeated` in the URL) makes the server
group read keep only groups of more than one record the person can read and
leave out the records with no value. It is offered where the level's axis can
tell its empty group apart; a view that cannot read it natively drops the flag
and keeps the grouping, while an authored default that asks for it there is
refused as a query error. `@angee/metadata` adds `ResourceQuery.toGroupRead`,
the `GroupRead` type, `groupAxisId` and `groupSpecsFrom`. A page's `bulkActions`
receives `collection.selectedGroups`, the selection split by its innermost
groups (`SelectedGroup`, `SelectedGroupLevel`), absent on an ungrouped list and
`null` when a selected row cannot be placed. `Button` and `SelectionBarAction`
take a `disabledReason`: a reason disables the verb, which stays focusable, and
is its tooltip and accessible description; `null`, `false` and an empty string
are no reason.
