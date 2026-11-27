---
"@angee/ui": patch
---

`SplitPane`'s recipe (`splitPanesVariants`, its `pane` slot) no longer carries
`overflow-hidden`, a class that never applied: react-resizable-panels sets
`overflow: auto` inline on the element that takes the pane's class, and an inline
style wins over any class that is not `!important`. A pane scrolls its content as it
did, and nothing computed changes; the pane's docstring now says so. A record in the
document presentation that a list opens in its split placement scrolls in the
content pane of the list's own workbench, as one opened inline scrolls in the list's
record sheet; a record in the workspace presentation, or with a full-bleed pane
active, scrolls inside the form in either placement. `Workbench`'s `scrollMode`
docstring now says that the prop does nothing (both values compute the same layout),
and the console layout no longer passes its default.
