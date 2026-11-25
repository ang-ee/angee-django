---
"@angee/ui": patch
---

A record form's create no longer displays a field its save cannot write that
holds an empty object, as it already did not display one holding no value, a
text of blanks or an empty list. What a record form displays now reads its own
fact of holding nothing to display (`holdsNothingToDisplay`: `null` or
`undefined`, a text of blanks only, an empty list, an empty object), where it
read the rule check's, for which an empty object is a value. So a read-only
JSON field a create seeds with `{}`, as a feed's configuration or an
integration's sync progress and last sync summary are, is no longer shown as an
empty object, and a group left with nothing to show is left out. An edit
displays every field, as before. What the form checks is unchanged (a required
JSON field holding `{}` is not missing), and so is what a create sends.
