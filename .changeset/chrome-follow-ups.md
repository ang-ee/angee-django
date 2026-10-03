---
"@angee/ui": patch
---

Keep top-bar app menus within the available width by moving excess entries into
More, retaining the current menu in the last visible slot and the app page when
all entries overflow. Single-child menus keep the same label and destination in
the row and More.

Add an expansion toggle at the expanded rail header's edge. `AppBrandProps` now
accepts and forwards anchor props and refs so shared tooltips can compose the
brand link. Icon-only rail links show their name and interaction hints, followed
by the developer description when developer mode is enabled.
