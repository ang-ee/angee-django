---
"@angee/ui": patch
---

The Links pane says it is empty when it is. A related group with neither rows
nor a verb is left out of the pane, and the pane showed its empty state only
when no group was eligible at all. A record whose eligible groups were all left
out (each counted nothing and offered no verb) showed "Links 0" over a blank
panel. The pane now shows the same empty state then.

Each group tells the pane whether it has rows or a verb, since only the group
knows its row count and its verb. A group that is still counting is taken to
show something, so the empty state does not flash while counts load, and the
list that holds no row is hidden, and so out of the accessibility tree, while
the empty state stands for it.
