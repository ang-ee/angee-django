---
"@angee/ui": minor
---

A record form offers a create-only implementation selector once more while the
record its draft was made against stores a key that is not registered
(`repairChoosable`, passed to `withModeLockedFields` as `choosable`). The lock
reads that record, held while the form holds edits and moved by an accepted
save, so neither a pick nor a re-read or another save meanwhile re-locks it. Choosing an implementation there
sets what the server says it sets on that record (the resource's repair read,
named by its metadata's `roots.repairChoices`: the config it carries over), so the save sends the selector with the config the
person sees. The read is `<res>_repair_choices(id, field)` on the resource's own
schema, answering under that root; each choice in it has the insert's choice
shape (`key`, `category`, `defaults`, `config_schema`), its `defaults` the
config carried over; a save that would send that config without its selector is refused
on the client. Its mark reads "Its implementation cannot run until another is
chosen. Once saved, the choice cannot be changed." Another key may be chosen
before saving, never while a save is pending. After the save the selector is
locked again. The repair asks that read through `@angee/refine`'s operation
documents (the `repairChoices` kind the host's codegen generates per
resource), so a resource without the root, or a host that
generated no document for it, offers no repair; the insert's `BaseImplChoices`
read is unchanged. A person the create flow
does not admit, or the update refuses, sees the selector read-only with the
server's words under the field. While the answer is pending the selector is
`loading`; a refused answer keeps it read-only with the server's words under it,
said once, beside the field's own description, and once, as the field's error,
where a refused save says the same sentence (the control's description reads it
once, and no hidden copy is left for a screen reader's browse mode, except the one
a read-only field's mark is described by). The server's refusal of a save
stays under the field across the re-read it causes, until the person changes
that field or discards. A field bound through `BoundDescriptorField` takes the
same states and lock. New public API: `WidgetControlProps.loading`,
`LabeledDescriptorField`'s `loading`, and on `FormViewSaveSurface` `editBasis`
(the record the draft was made against: a save sends its revision and the lock
reads it), `recordField` and `repairState`. A save that would send the repair's
config without its selector (its condition or its group hides it now) is
refused on the client in words naming the selector.
A page's own `createOnly` never gets the repair, and a page cannot unlock what
the model's metadata locks.
