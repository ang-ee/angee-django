---
"@angee/ui": patch
---

A record form offers a create-only implementation selector once more while the
key its record stores is not registered (`storedImplementationUnregistered`,
passed to `withModeLockedFields` as `choosable`; the lock reads the stored row
only, so a pick never re-locks it). Choosing an implementation there sets what
the server says it sets on that record (`impl_choices` with `record`: the config
it carries over), so the save sends the selector with the config the person
sees. Its mark reads "Its implementation cannot run until another is chosen.
Once saved, the choice cannot be changed." The lock reads the stored key only, so
another key may be chosen before saving. After the save the selector is locked
again.
