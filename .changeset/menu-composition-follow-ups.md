---
"@angee/app": minor
"@angee/ui": minor
"@angee/refine": minor
---

A page renders in the place of the menu node it declares, and a route's menu
anchor breaks ties between equal matches (`createTanStackRouterProvider` takes
the resource identifier). A menu entry or container child may name
`requires: "<app_label.ModelName>#<permission>"`, which leaves out what the
identity cannot use but never a route. Resource ownership follows addon
dependency, not the selected rail; `recordMatch` accepts a list of values;
app-wide mount aliases drive record hrefs; `?app=` stays in the URL across links
and redirects.
