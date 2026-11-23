---
"@angee/ui": minor
---

`DescriptorFieldList`, the field list `MutationDialog` and `ActionFormDialog`
render, follows the record form's rules. With the new `unregisterHidden`, which
both dialogs set, a field hidden by `showWhen` is not submitted and takes its
initial value back when it shows again. A dialog honours a field's
`prefillPreserveDirty` and `prefillReplace` as a record form does. A field's
`resolve` now keeps its declared `name` in a record form too, as it already did
in a dialog. `DescriptorFieldList` must be rendered inside an
`ActionFormProvider`. Upgrade: pass `fields` where you passed `resolvedFields`,
which is removed, and wrap a hand-built `DescriptorFieldList` in
`<ActionFormProvider {...form}>`, with the `form` that `useActionForm` returns.
