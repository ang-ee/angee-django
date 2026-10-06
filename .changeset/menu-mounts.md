---
"@angee/app": minor
"@angee/ui": minor
---

An addon composes other addons' pages under its own app in two ways: `include`
absorbs an app (it leaves its place on the rail), and the new `mount` borrows a
page. A menu node `{ parent, mount: "<route>", path }` gets an alias route named
after it (`<id>`, plus `<id>.record` for the route's record child) at its app's
`path` (a root's new `path`, `/<id>` by default) joined with its own, reusing the
mounted route's page, detail page and model; the source app keeps its page. A
node targets one of `route`, `mount` and `to`, and an addon mounts only routes
of addons it depends on. A mount's `defaultResourceView` and `recordMatch` go
onto the alias, and inside it `useRouteHref()` builds the mounted route's names
as the alias's (`aliasRouteHref` in `@angee/ui/runtime`), so the page's links
stay in the borrowing app.

Record ownership is global: a record opens at the `recordMatch` claim its row
matches from every app, then at the active app's own claim, then canonically;
two matches on one resource and condition fail at boot. Vocabulary composes
every app on the page's trail, outermost first, and a vocabulary `app` may name
any app a trail holds (a root or an included app), as `when: { app }` does; a
platform root is no app. Two roots referencing one route no longer throw; the
route has no owning root unless `route.menu` names one.
