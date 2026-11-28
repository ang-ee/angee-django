---
"@angee/refine": minor
"@angee/ui": minor
---

`MutationDialog` reports a failure once: a refusal bound to a field stays under
that field with no fallback banner (`applyFormErrors` sets none while a rendered
field holds the error), and every call the dialog makes for its caller
(`validate`, `onSubmit`, `mutation`) receives the new `AuthoredMutateOptions`
(`@angee/refine`), which turn refine's generic error toast off for that call.
The dialog takes an authored `mutation` (with an optional `mutationResult`) as
an alternative to `onSubmit`, so `MutationDialogProps` is now a union of the
two. An authored mutate function takes the options as its second argument and
`ActionMutate` as its third; an action's `submit(values, context, options)`,
`useActionForm`'s `submit` and the dashboard store binding's `save` and
`createPersonal` receive them as well. `createMutationDialogTestDouble` runs
`validate` before it submits, as the dialog does. Upgrade: a `validate`,
`onSubmit`, action `submit` or store binding that makes an authored call passes
the options on to it (or hands that call to the dialog as `mutation`), so its
failure is not toasted twice; code reading `onSubmit` from `MutationDialogProps`
checks for it, since a dialog may carry `mutation` instead.
