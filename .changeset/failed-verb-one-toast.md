---
"@angee/ui": patch
---

A failed request of a verb bound with `useRecordActionMutation`, or settled
through `useActionResultMutation` or `useRecordChromeActionMutation`, is told
once. These hooks already toast a failed request themselves (the action bar
under the verb's label; the settle owner under the error's message), and the
data layer raised its own "Something went wrong" beside it: two toasts for one
failure. They now turn the data layer's own error notice off for their verb, as
the stack's rule for a surface that owns its error feedback asks; sign-in
errors are still handled by the data layer.

Not changed: `useActionOutcomeMutation`, which leaves the outcome to its
caller, still raises the data layer's notice unless its caller turns it off. A
caller that shows the failure itself (its own settle call, or a dialog's
`submit`, which is handed the option and must pass it on) and does not pass
`errorNotification: false` still tells a failed request twice; several do
today.
