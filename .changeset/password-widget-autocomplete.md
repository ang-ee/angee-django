---
"@angee/ui": patch
---

The `password` widget is the masked entry of a secret that is not the person's
own sign-in password, and tells the browser so with `autocomplete="new-password"`
when the field declares no `autoComplete` of its own, so a vendor key after a
name field is no longer filled with the saved sign-in. Its read form shows
nothing for an empty value instead of a mask. The `textarea` widget shows the
field's `placeholder`, and its label only when the field declares none.
