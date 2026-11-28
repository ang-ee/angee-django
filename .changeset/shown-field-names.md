---
"@angee/ui": minor
---

The fields a form shows have one name where a refusal is bound: the option of
`applyFormErrors` that lists them is `shownFieldNames` (on a descriptor form,
the names of what `shownFields` returns), and `useActionForm` takes it under the
same name and requires it. A reason under a path the list does not hold shows on
the banner as `path: reason`; `[]` states that the form shows no field a reason
can sit under. An inline action form that passed no list sent every reason to
its banner as `key: reason`, even a reason a field it draws already showed under
that field; one that states the fields it shows has each reason said once, at
its field, and a refusal brings focus to its first refused field or, once it
spreads the hook's `banner` on its banner, to the banner. `useDottedPathFieldErrors`
names its parameter the same way. `useController` is exported beside
`useWatch`, so an inline form whose control is not a native input (a select, a
date popover) hands the form that control as its focus target.

Upgrade: rename `fieldNames` to `shownFieldNames` on `applyFormErrors` and
`useActionForm`; a `useActionForm` caller that passed none passes the fields its
form shows, or `[]`, since an omitted list no longer compiles. An
`applyFormErrors` call without a list keeps every reason at its path and no
field's reason on the banner, for a caller that knows no drawn fields:
`createJsonSchemaResolver`'s errors and the workflow studio's projection of its
issues.

`useActionForm` reads its list, and `fieldSummary` with it, when the answer
lands, a plain list as a function alike: a field drawn or gone while the submit
was out is claimed, or left to the banner, as the form shows it then.
