---
"@angee/metadata": minor
"@angee/ui": patch
---

A resource field's metadata may carry `createOnly`: the field is a create-only
implementation selector, which its update input carries and its model's save
lets change only while the stored key is not registered. A record form reads it
as the field's `createOnly` (`fieldsWithMetadataDefaults`), so a page no longer
restates it, and a page's own `createOnly: false` cannot unlock it. `fieldChangeable`
says whether a person may change a field on a stored record (updatable and not
create-only); the board's lane drag asks it.
