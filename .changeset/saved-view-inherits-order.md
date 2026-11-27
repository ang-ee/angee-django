---
"@angee/ui": minor
---

A saved or shipped view with no `sort` of its own keeps the order the list declares, and
only an explicit `sort: null` clears it. Before, applying such a view cleared the list's
order, while opening the same shipped view as a route's default kept the declared order,
and the view showed active whatever the list was sorted by. Now the three agree:
applying it sets the order the collection declares under the view's preset (the
shipped view's own `sort`, else the list's declared order), opening its preset does the
same, and it shows active while the list holds that order and stops when the person
sorts otherwise; a view of the inherited order and an unordered view are never active
together. A view saved while the list's view holds no order of its own stores no `sort`
and keeps inheriting; a list mounted by `ListView` or `ResourceList` seeds its declared
order, so a view saved there stores it.

`ResourceViewContextValue` gains `inheritedSorting`, the order such a view keeps on that
list, `undefined` when the list's view declares none; a hand-built context value supplies
it.

**Upgrade.** Four consequences, none needing code:
- a shipped view without a `sort`, picked in the view switcher, keeps the list's declared
  order instead of leaving the list unsorted;
- a view saved from a cleared order stores `sort: null`;
- a view without a `sort` is active only while the list holds the inherited order;
- a view saved before this change from a list whose order was cleared stored no `sort`,
  so it now restores the declared order: to keep it unordered, clear the order and save
  it again (it then stores `sort: null`).
