---
"@angee/ui": patch
---

A record form offers a create-only implementation selector once more while the
record its draft was made against stores a key that is not registered
(`repairChoosable`, passed to `withModeLockedFields` as `choosable`). The lock
reads that record, held while the form holds edits, so neither a pick nor a
re-read or another save meanwhile re-locks it. Choosing an implementation there
sets what the server says it sets on that record (`impl_choices` with `record`:
the config it carries over), so the save sends the selector with the config the
person sees; a save that would send that config without its selector is refused
on the client. Its mark reads "Its implementation cannot run until another is
chosen. Once saved, the choice cannot be changed." Another key may be chosen
before saving, never while a save is pending. After the save the selector is
locked again. The repair asks `BaseImplRepairChoices`, a document of its own, which
needs a server whose `impl_choices` takes `record`; the insert's
`BaseImplChoices` is unchanged. While the answer is pending the selector is
`loading`; a refused answer keeps it read-only with the server's words under it.
A page's own `createOnly` never gets the repair, and a page cannot unlock what
the model's metadata locks.
