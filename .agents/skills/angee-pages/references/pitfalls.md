# Page, route, navigation and rendering pitfalls

Traps moved from the frontend guide's Pitfalls section. The contracts they lean on stay in [Rules](../../../../docs/frontend/guidelines.md#rules) and [Containers](../../../../docs/frontend/guidelines.md#containers); this file adds no rule.

- **A container query never matches the container itself:** a rule for `.x` inside `@container` on `.x` queries an ancestor container, usually none. Put width-dependent rules on the container's children, as [`.resource-toolbar`](../../../../packages/ui/src/styles/index.css) does.
- **A render callback may only read fields some column declares or the `ListView fields={[…]}` extras name:** the [selection contract](../../../../docs/frontend/guidelines.md#rules) fetches nothing else — an undeclared read is `undefined` on every row (a link built from it throws, a caption silently blanks). Still null-guard values a row may legitimately lack.
- **Layouts bind their own schema** ([`RefineLayoutConfig.schema`](../../../../docs/frontend/guidelines.md#rules)):
  console-only fields need the console client — set `defaultSchema: "console"` on
  the layout that renders them and pin the public/login layout to `public`.
- **Route code-splitting touches three things.** (1) `defaultPendingComponent` is
  the *app-wide* pending surface — it renders for every non-root match while its
  chunk loads, and (after `defaultPendingMs`) for any future `loader`-bearing
  route, not just lazy pages. Intent preloading runs each matched route's
  `beforeLoad` on hover (an already-active ancestor with the current search) and
  would run a future `loader` too, at most once per `defaultPreloadStaleTime`,
  which is why a `beforeLoad` only reads and redirects
  ([routed pages](../../../../docs/frontend/guidelines.md#rules)). (2) The addon-index imports in `runtime/web/app.ts`
  stay eager — manifests compose synchronously; only each manifest's *page*
  imports go through `lazyRouteComponent`. (3) A test that renders a routed page
  *through the router* (`createApp`/`RouterProvider`) must await the lazy boundary
  (`findBy*`); a test that imports the page component directly is unaffected, and a
  manifest assertion (`component` is a function) still holds for a lazy component.
  Project TypeScript configs must allow importing `.ts`/`.tsx` extensions because
  the generated runtime imports addon index source files by their package export
  paths.
- **Shared/generic icon glyphs live in the base `chrome/icon-registry.ts`** —
  composition is fail-fast on id and base composes first
  ([glyph rule](../../../../docs/frontend/guidelines.md#rules)), so adding a name
  to `baseIcons` collides with any addon already contributing it. The collision
  throws only at app boot — `typecheck`/`build` miss it — so verify the full
  composed app still boots after touching `baseIcons` or an addon's `icons`;
  `tsc` alone cannot catch it. Use the browser verification path in
  [Checks](../../../../docs/checks.md#browser-verification).
