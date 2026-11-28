---
"@angee/ui": patch
---

A record form mounts a pane the same way whether it has one pane or several, so
a pane that stays selected, or is kept mounted (`keepMounted`), is no longer
rebuilt when another pane comes or goes. Before, when the number of panes
crossed between one and several while the form was open (a value giving a pane
its first field or taking its last, a record tab's `visibleWhen` after a save
or a fresh read of the record), the remaining pane's whole content was mounted
again: a field being typed in there lost its node and its focus, and a
component in it, such as a tab kept mounted, lost its own state. A lone pane is
still the plain region beneath the sheet under its heading, with the same
markup and no tab role, and several panes are still tab panels labelled by
their tabs.

Which pane is active is unchanged. A pane that appears ahead of a lone pane the
person had not chosen takes the selection, and a pane the person had chosen
that is dropped gives way to the first pane and is selected again when it
returns; a pane that loses the selection and is not kept mounted is unmounted,
as any inactive pane is. A create that is saved still mounts its form again,
as before.
