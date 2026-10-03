---
"@angee/ui": patch
---

Keep top-bar app menus within the available width by moving excess entries into
More, retaining the current menu in the last visible slot. The bar leads with the
app's name as a title, set apart from its menus. Single-child menus keep the same
label and destination in the row and More.

The breadcrumb strip appears only for nested navigation (a record and deeper),
starting at the current menu page; `useNestedBreadcrumbItems` owns that trail.
Record references in forms follow their route in-app instead of reloading the
console.

Add an expansion toggle at the expanded rail header's edge. `AppBrandProps` now
accepts and forwards anchor props and refs so shared tooltips can compose the
brand link. Icon-only rail links show their name and interaction hints, followed
by the developer description when developer mode is enabled.
