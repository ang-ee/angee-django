---
"@angee/ui": minor
---

`FormView`, and so `Form` and every registered form, takes an optional
`renderUnavailable(id, unavailable)`: what a saved record's form shows when its
id reads no record (a load error still shows its error), such as a redirect to
the record that replaced it. `unavailable` is the default "Record unavailable"
state, to render when there is nothing else to show. Without it the default
shows, as before.
