---
"@angee/app": minor
"@angee/ui": minor
---

Addons rearrange menus through one dict language layered by addon dependency:
`menus` accepts `{ id: entry }` (own-namespace keys declare; other keys alter a
dependency's node with `include`/`flatten`, `remove`, `hide`, `only`,
`sequence`, `before`/`after`), the legacy list form still works, and the
deployment's `ANGEE_UI.menus` applies last. `remove` makes the console pages
only removed nodes reached unavailable: they redirect home and `routeHref.maybe`
skips them. Hidden nodes leave the rail but stay in the palette and keep their
pages reachable (`ChromeMenuItem.hidden`). `createApp(...).explain` reports the
shell and menu provenance, removals, hidden nodes, unavailable routes and
diagnostics. `createRouteHref` accepts the unavailable routes, and
`developmentMode` moves to `@angee/ui/lib/development-mode`.
