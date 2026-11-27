---
"@angee/metadata": minor
"@angee/ui": patch
---

A resource field's metadata may carry `createOnly`: the field is a create-only
implementation selector, which its update input carries and its model's save
lets change only while the stored key is not registered. A record form reads it
as the field's `createOnly` (`fieldsWithMetadataDefaults`), unless the page
declares `createOnly` itself, so a page no longer restates it.
