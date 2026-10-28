---
name: angee-pages
description: "Composes a page in an Angee addon web fragment: resourcePageRoutes with lazy route components, menus and glyphs, ResourceList/List/Form, ListView, RowsListView, container contributions, the addon i18n namespace, and data via @angee/refine and @angee/gql. Use for \"add a page\", \"list view\", \"record page\", \"aside tab\", \"form#actions\"."
---

# Angee Pages

Run `angee-design` first for the owner map; `angee-addon` creates the fragment (`web/package.json`, `index.tsx`,
`i18n.ts`, `index.test.ts`). This skill adds a page to an existing fragment: a routed page is a route instance of a
design-system shape that declares intent and composes owners.

## Use when

- "Add a page / list / record page / settings page for `<app>.<Model>`."
- "Add a tab, section, verb, header control or Links group to `<Model>`'s record page."
- "Contribute a column, facet or search shortcut to another addon's page."
- "Add a story or test for `<Name>Page`."
- Not for the Python resource or node (`angee-graphql`) nor manifest and package wiring (`angee-addon`).

## Owners

| File or module | Owns | Reference |
|---|---|---|
| `web/src/index.tsx` (`defineBaseAddon`) | Routes, menus, icons, i18n bundle, forms, widgets, containers: the one greppable seam | `addons/angee/tags/web/src/index.tsx:35-53` |
| `packages/app/src/define-base-addon.ts` | `BaseAddonRoute`, `resourcePageRoutes` (list + `$id` pair, `console` layout), `defineBaseAddon` | `:20`, `:66`, `:162` |
| `packages/app/src/define-addon.ts` | `AddonManifest` fields and `composeAddons` (fail-fast layering) | `:139`, `:295` |
| `web/src/views/<Name>Page.tsx` | One routed page: declarations, container reads, domain glue only | `addons/angee/tags/web/src/views/TagsPage.tsx:13` |
| `packages/ui/src/views/resource/resource-list/public.tsx` | `ResourceList`: routed record navigation, inline/modal record, create | `:236` |
| `packages/ui/src/views/resource/list-view/frame.tsx`, `RowsListView.tsx` | `ListView` (server collection, grouping, views, `rowActions`), `RowsListView` (bounded in-memory rows) | `:39`, `:115` |
| `packages/ui/src/views/form/registered-form.ts` | `registerForm`: the complete form a page passes as `form=`; authored under `angee-forms` | `:22` |
| `packages/ui/src/runtime/containers.ts`, `packages/app/src/containers.ts` | `ContainerChild`, `ContainerEntry`, `useContainer`; the compiler | `:93`, `:127`, `:432`; `:44` |
| `web/src/i18n.ts`, `packages/ui/src/i18n/index.ts` | The addon namespace bundle and `createNamespaceT` | `addons/angee/tags/web/src/i18n.ts`, `:45` |
| `web/src/documents*.ts`, `packages/refine/src/dialect/authored-hooks.tsx` | Typed `graphql()` documents, `useAuthoredQuery`/`useAuthoredMutation` | `addons/angee/decisions/web/src/documents.console.ts`, `:92`, `:213` |

## Reuse first

- Copy shapes from `addons/angee/tags/web` (resource page with own containers) and `examples/addons/example/notes/web` (consumer page, e2e target); start a fragment from `templates/addons/web` ([pitfall](../../../docs/frontend/guidelines.md#pitfalls)).
- `ResourceList` + `List`/`Form` own the resource page; `ListView` owns grouping, boards, search, `rowActions`, trash; `RowsListView` owns bounded in-memory rows ([rules](../../../docs/frontend/guidelines.md#rules)).
- Record surfaces are `FormView` via `ResourceList`'s `Form` child or `form`; `RecordHeader`, `MetaGrid`, `Statusline`, `EmptyState`, `ErrorBanner`, `LoadingPanel` are the fragments ([ontology](../../../docs/frontend/guidelines.md#component-ontology)).
- A tab, verb, header control, drawer or Links group is a child of a [framework container](../../../docs/frontend/guidelines.md#framework-containers); search `packages/ui/src/catalog.ts` before any new element.
- Links: `useRouteHref` (`packages/ui/src/runtime/runtime.ts:474`), `useResourceRecordHref` (`:403`), `useRouteParam` (`packages/ui/src/views/resource/resource-routing.ts:153`).
- Reads: the surfaces for resources; `graphql()` documents with `useAuthoredQuery` for bespoke operations; `useActionMutation` (`packages/refine/src/dialect/hooks.tsx:540`) and `useActionResultRun` (`packages/ui/src/views/resource/action-result-run.ts:52`) for verbs.
- `useContainer` + `containerContents`/`ContainerOutlet` (`packages/ui/src/lib/container-outlet.tsx:17`) read contributions; no page registry.
- Tests: `expectValidBaseAddon`, `containerChildren`, `ShellPageTestProviders` (`packages/app/src/testing.tsx:101,167,45`); stories: `RoutedRuntimeFixture` (`packages/storybook/src/stories/runtime-fixtures.tsx:211`).

## Steps

1. **Pick the surface from the data.** Resource with a record form → `ResourceList` with `List` + `Form` (step 5). Resource without a record (grants, runs) → `ListView` with `columns`/`rowActions`. Bounded computed rows with no resource → `RowsListView`. A record page that replaces the list → `resourcePageRoutes(..., { detailComponent })`. A list beside its detail is still `ResourceList` ([master-detail](../../../docs/frontend/guidelines.md#pitfalls)).
   **Owner rule:** the row-model choice follows dataset size, not data origin ([client/server boundary](../../../docs/frontend/guidelines.md#rules)).

2. **Declare the route pair in `index.tsx`.** The page stays lazy; the manifest never imports it.

   ```tsx
   const tagsRoutes: readonly BaseAddonRoute[] = [
     ...resourcePageRoutes(
       "tags.tags",
       "/tags",
       lazyRouteComponent(() => import("./views/TagsPage"), "TagsPage"),
       "tags.Tag",
       { detailName: "tags.tag", menu: "tags.tags" },
     ),
   ];
   ```

   If the collection has no resource (a parameterized projection) → pass `undefined` with `param`/`detailComponent`, as `work.triage-hub` does (`addons/angee/work/web/src/index.tsx:36-47`). Omit `detailName` unless an established name must survive.
   **Owner rule:** the route is the only place a URL is spelled; menus and links name the route ([rules](../../../docs/frontend/guidelines.md#rules)).

3. **Add the menu node and its glyphs.** One root per addon; `group: "platform"` places it in Settings.

   ```tsx
   const tagsMenu: MenuDeclarations = {
     [TAGS_ID]: { label: "Tags", icon: "tags-group", group: "platform" },
     "tags.tags": { label: "Tags", icon: "tags-tag", route: "tags.tags", parent: TAGS_ID },
   };
   // defineBaseAddon({ ..., icons: { "tags-group": TagsIcon, "tags-tag": TagIcon } })
   ```

   A page under another app's root is a `mount`, never a copied route ([absorb or borrow](../../../docs/frontend/guidelines.md#pitfalls)).
   **Owner rule:** lucide components enter through `icons:` and render as `<Glyph name>`; a component never imports `lucide-react` ([rules](../../../docs/frontend/guidelines.md#rules)).

4. **Route copy through the addon namespace.** `i18n.ts` holds only keys components resolve (`addons/angee/tags/web/src/i18n.ts`); manifest `label:` strings are chrome data and stay plain ([route every user-facing string](../../../docs/frontend/guidelines.md#rules)); plurals are native `key_one`/`key_other`.
   **Owner rule:** `ui` is reserved and metadata labels come from the SDL ([reserved namespace](../../../docs/frontend/guidelines.md#reserved-translation-namespace)).

5. **Write the page as declarations.**

   ```tsx
   export function TagsPage(): React.ReactElement {
     const t = useTagsT();
     const scopeFacetEntries = useContainer("tags.tags#facets");
     const scopeColumnEntries = useContainer("tags.tags#columns");
     return (
       <ResourceList resource={TAG_MODEL} form={tagForm} placement="inline" routed>
         <List resource={TAG_MODEL} defaultGroup={{ field: "is_archived" }}>
           {containerContents(scopeFacetEntries)}
           <Column field="name" header={t("col.name")} />
           <Column field="color" header={t("col.color")} />
           {containerContents(scopeColumnEntries)}
           <Column field="updated_at" />
         </List>
       </ResourceList>
     );
   }
   ```

   `routed` hands record open/create/close to the `$id` child. `presetIds`, `search`, `createAction`, `emptyContent`, `defaultGroups` go on `List`; `recordSmartButtons`, `contextFields` on `ResourceList`. `form={tagForm}` is the resource's registered form: author it with `angee-forms`, never inside the page.
   **Owner rule:** pages own no table mechanics, route params, cache or loading/error surfaces ([routes and pages stay thin](../../../docs/frontend/guidelines.md#rules)).

6. **Read and write beyond CRUD through typed documents.** The file name picks the schema: `documents.ts`/`documents.console.ts` → console, `documents.public.ts` → public.

   ```ts
   // documents.console.ts
   import { graphql, type DocumentType } from "@angee/gql/console";
   export const DecisionDocument = graphql(`
     query DecisionDetail($id: String!) { decisions_by_pk(id: $id) { ...DecisionCardFields } }
   `);
   // InboxPage.tsx
   const query = useAuthoredQuery(DecisionDocument, { id: recordId }, { models: DECISION_MODELS });
   if (query.isLoading) return <LoadingPanel />;
   if (!query.data?.decisions_by_pk) return <ErrorBanner description={t("decision.unavailable")} />;
   ```

   Row verbs are `rowActions` descriptors (`defineRowAction`, `addons/angee/iam/web/src/role-grants.tsx:18`); record verbs are `#actions`/`#actions-menu` children; `models` names what the resolver reads so live pushes invalidate it.
   **Owner rule:** result and variable types come from the generated `TypedDocumentNode`; never a hand-written interface or a hand-built Hasura `where` ([typed operations](../../../docs/frontend/guidelines.md#rules)).

7. **Contribute before you copy.** A tab, section, verb, header control, drawer, notice or Links group on another owner's page is a container child in `index.tsx`:

   ```tsx
   containers: {
     "form#chrome": {
       "notes.record-chrome": { sequence: 10, content: <RecordChrome /> },
     },
     "auth.login#password-help": {
       "notes.demo-logins": { content: <DemoForgotPasswordHint /> },
     },
   },
   ```

   Addresses, model inheritance, `when` narrowing, `impl` variants, the aside, view kinds and tests: [references/containers.md](references/containers.md).
   **Owner rule:** a child id is namespaced and declared once; vary it by the row's declared implementation, never by probing the row ([containers are additive](../../../docs/frontend/guidelines.md#rules)).

8. **Test the declarations; give the page a story.** `index.test.ts` keeps `expectValidBaseAddon(addon)` plus addon-specific assertions (`addons/angee/tags/web/src/index.test.ts`); a page test calls the page as a function and parses it with `pageChildren`/`pageElementProps`/`parsePageColumns` (`addons/angee/tags/web/src/views/TagsPage.test.tsx:35-48`). A data-bound story mounts `RoutedRuntimeFixture` with `storySchema` fixtures, `routeHref`, `routesByResource` and `parameters.angeeOwnRuntime` (`addons/angee/decisions/web/src/InboxPage.stories.tsx:12-33,80-83`); Storybook discovers `web/src/**/*.stories.tsx` from the stack root.
   **Owner rule:** a routed test awaits the lazy boundary; a direct page import does not ([route code-splitting](../../../docs/frontend/guidelines.md#pitfalls)).

## Do not

- Hand-roll a list, grid, board, form or detail when `ResourceList`/`ListView`/`RowsListView`/`FormView` own that shape; extend the owner in `@angee/ui` instead ([shared primitives](../../../docs/frontend/guidelines.md#rules), [constitution](../../../AGENTS.md#constitution)).
- Render raw `<button>`/`<table>`/`<form>`, a lucide icon, `toLocale*`, a local `tv()` recipe or tone map in an addon: `@angee/ui` owns each and the ontology guard fails (`packages/app/src/ux-ontology-guard.ts:1-7`, [guards](../../../docs/frontend/guidelines.md#ownership-boundaries)).
- Decide permissions, business state or persistence in React; `visibleWhen` reads projected admission ([client-side gates](../../../docs/frontend/guidelines.md#rules), [red flags](../../../docs/guidelines.md#boundary-leaks-and-detail-driven-policy)).
- Spell a URL, `navigate({ search: … as never })` or `useParams(...) as {…}`; use `useRouteHref`, `updateRouteSearch`, `useRouteParam` ([rules](../../../docs/frontend/guidelines.md#rules), [pitfalls](../../../docs/frontend/guidelines.md#pitfalls)).
- Import `@angee/gql` in a framework package, or eager-import a page into the manifest ([schema boundary](../../../docs/frontend/guidelines.md#rules), [code-split pages](../../../docs/frontend/guidelines.md#rules)).
- Split one collection into two lists, or mirror render data into state ([one collection is one list](../../../docs/frontend/guidelines.md#rules), [derive during render](../../../docs/frontend/guidelines.md#rules)).
- Poll with `setInterval` or add a second cache; opt the model into `changes()` ([live updates](../../../docs/frontend/guidelines.md#rules)).
- Register a module-global at runtime or keep an addon-local registry beside containers ([compose at build time](../../../docs/frontend/guidelines.md#rules)).
- Put product copy in the `ui` namespace or override shared copy ([reserved namespace](../../../docs/frontend/guidelines.md#reserved-translation-namespace)).
- Gate a page on a caveated REBAC subject: `angee.E024` rejects caveats (`addons/angee/base/checks.py:37`); the permission owner declares a field-backed conditional relation and the page reads projected admission.

## Verify

Done means: `expectValidBaseAddon` passes with the new routes; the fragment's own typecheck and vitest pass; the guardrails test and ontology guard pass over the addon root; the composed web typecheck passes; the page renders in the live app with loading, empty, error and record states exercised and screenshotted ([browser verification](../../../docs/checks.md#browser-verification)).

- In a lane (jj workspace: no host, database or JS install) no web check runs; do the primitive-drift scan and read the guard rules ([frontend checks](../../../docs/frontend/guidelines.md#checks)).
- On integrated `src`: per-package typecheck and test, the guardrails test, the composed fragment typecheck after SDL + codegen, Storybook `catalog:check` when exports or stories change ([frontend packages](../../../docs/checks.md#frontend-packages-and-addon-fragments), [composition](../../../docs/checks.md#composition-and-schema)).
- Loop: `.agents/skills/slice/SKILL.md`. False greens: the composed web typecheck is not the per-package one that covers test files (both rows in [frontend packages](../../../docs/checks.md#frontend-packages-and-addon-fragments)); `@angee/gql` is stale until codegen reruns ([generated documents](../../../docs/frontend/guidelines.md#pitfalls)); a glyph id collision throws only at app boot ([shared glyphs](../../../docs/frontend/guidelines.md#pitfalls)); browser e2e is manual, not CI ([what CI runs](../../../docs/checks.md#what-ci-actually-runs)).

## Pitfalls

- A render callback reading a field no column declares gets `undefined` on every row; declare it in `fields={[…]}` ([pitfalls](../../../docs/frontend/guidelines.md#pitfalls)).
- A route whose `menu` anchor and path fall in different places (an app and Settings) fails composition; put the path under the anchor's ([page place](../../../docs/frontend/guidelines.md#pitfalls)).
- Two route-backed collections on one page share the unnamespaced query unless each names its state owner ([collection state](../../../docs/frontend/guidelines.md#pitfalls)).
- `useContainer` on an address nobody declared only warns in development; declare the address on the addon's own node (`packages/ui/src/runtime/containers.ts:439-442`).
- A `record#aside` child never shows on a create route or a route whose param names no model ([context aside](../../../docs/frontend/guidelines.md#context-aside)).
- A `form#sections` child is a direct `Group`/`Tab`/`Action` element; the parser renders no wrapper to find it ([addresses and kinds](../../../docs/frontend/guidelines.md#addresses-kinds-and-inheritance)).

## Read next

- [Rules](../../../docs/frontend/guidelines.md#rules), [Containers](../../../docs/frontend/guidelines.md#containers), [Pitfalls](../../../docs/frontend/guidelines.md#pitfalls); forms: `angee-forms`
- [Package layering](../../../docs/frontend/guidelines.md#package-layering), [Component ontology](../../../docs/frontend/guidelines.md#component-ontology), [Glossary: Frontend](../../../docs/glossary.md#frontend)
- [App vocabulary and shipped views](../../../packages/app/README.md#app-vocabulary-and-shipped-views), [e2e](../../../docs/frontend/e2e.md)
- Skills: `angee-design` (first), `angee-addon`, `angee-forms`, `angee-graphql`, `angee-permissions`, `slice`

## Sources

<!--
Docs: AGENTS.md (Area Rules: Packages); docs/guidelines.md; docs/frontend/guidelines.md:15-186,187-1070,1071-1497,1553-2042; docs/glossary.md:441-514; docs/checks.md:9-30,168-358; docs/frontend/e2e.md headings; packages/app/README.md:110-200.
Code: packages/app/src/{define-addon.ts:1-310,define-base-addon.ts,containers.ts:1-50,testing.tsx:45-170,ux-ontology-guard.ts:1-60,architecture-guardrails.test.ts:715-760}; packages/ui/src/{runtime/containers.ts:16-260+425-452,runtime/contracts.ts:195-240,runtime/runtime.ts:403-490,views/resource/list-view/frame.tsx:1-80,views/resource/RowsListView.tsx:100-135,views/resource/resource-list/public.tsx:60-236,views/resource/List.tsx:40-80,views/resource/RowActions.tsx:127-190,views/resource/resource-routing.ts:145-160,views/form/registered-form.ts:22,lib/container-outlet.tsx:17-26,i18n/index.ts:30-60}; packages/refine/src/{index.ts:53-120,dialect/authored-hooks.tsx:92+213,dialect/hooks.tsx:540}; packages/metadata/src/index.ts; packages/storybook/{.storybook/main.mjs:1-40,src/stories/runtime-fixtures.tsx:144+211-240}.
Addons: addons/angee/tags/web/**; examples/addons/example/notes/web/**; addons/angee/decisions/web/src/{InboxPage.stories.tsx,InboxPage.tsx:60-100,documents.console.ts}; addons/angee/workflows/web/src/WorkflowsPage.stories.tsx; addons/angee/work/web/src/index.tsx; addons/angee/iam/web/src/{views/GrantsPage.tsx,role-grants.tsx:18-41}; addons/angee/{timeline,projects}/web/src/index.tsx; templates/addons/web/**.
Doc gaps: no story-authoring guide; no page-test guide.
Mismatches: (1) examples/addons/example/notes/web/src/NotePage.tsx:75-77 reads the record id with useParams({strict:false}) and index.tsx:12-26 declares the route pair by hand, while docs/frontend/guidelines.md:333 prescribes resourcePageRoutes and :1994-1995 names useRouteParam (packages/ui/src/views/resource/resource-routing.ts:153). (2) ContainerKinds.regions (`chrome.app-menu`, packages/ui/src/runtime/containers.ts:36) is absent from the Framework containers table at docs/frontend/guidelines.md:1240-1266. (3) templates/addons/web/template/src/index.tsx.jinja passes the bare resource_name (`Note`) as the route resource; guidelines.md:378 says durable manifests store the canonical qualified label.
-->
