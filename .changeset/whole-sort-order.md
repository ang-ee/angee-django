---
"@angee/ui": minor
---

A list keeps its whole sort order in the URL and in a saved view. Before, both kept
only the first key: on a list grouped by status and ordered by status, then rank, a
press on Rank turned it descending, the URL wrote `sort=status:asc`, and the list
read back status alone, so Rank showed "not sorted" and the order was lost on reload,
on a shared link and in a saved view; a saved view of a list ordered by rank and then
title restored rank alone and did not show active right after it was saved.

The URL's `sort` now holds every key, in order, as a comma list:
`sort=status:asc,rank:desc`. A one-key link reads exactly as before. A link whose
order cannot be read (`rank:asc,`, `rank:asc:title`, a field the resource cannot sort
by, or a field named twice) reads nothing and offers the list's reset, as an unreadable
key always has.

**Changed contract.** A saved view's `sort` (`ResourceViewFavorite`, and so a shipped
`ResourceViewPreset`) and `ResourceViewInitialState.sort` are one `{ field, dir }` or a
list of them; `null` still clears. A saved view is written with a list only when its
order has several keys, so every one-key saved view and shipped view is stored exactly
as before. **Upgrade:** code that reads `sort.field` from a saved or shipped view
handles a list (`Array.isArray(sort)`); a declaration that names one key needs no
change.

The favorites preference document (`resource-view.favorites`) is now version 2. A
version 1 document reads as before and is written as version 2 at its next change. A
tab still running an earlier bundle reads a version 2 document as an unknown version
once it receives the version 2 document (a live update or a reload): it then shows no
saved views and writes none until it runs the new bundle, so it never drops a view whose
order it cannot read.
