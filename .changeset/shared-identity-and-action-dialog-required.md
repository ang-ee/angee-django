---
"@angee/app": minor
"@angee/ui": minor
---

`readSharedIdentity(queryClient, authProvider)` (`@angee/app/providers/auth`)
reads the shared identity entry and reuses its last answer, as the route gate and
the auth provider's `check` and `getPermissions` do. `ActionFormDialog` enforces
the required fields of an action's form definition, static or computed from the
values: a missing field shows its error and the submit button stays disabled
until it is filled, while a definition with its own resolver keeps its own value
checks.
