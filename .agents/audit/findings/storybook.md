# Storybook structural audit — packages/storybook

Reviewed against AGENTS.md (Constitution: find-the-owner, DRY, prefer deletion),
docs/guidelines.md (red flags: "code is bigger instead of smarter"; "Name So Code
Can Be Found"), docs/frontend/guidelines.md ("Use shared page, view, form, table,
widget, and shell primitives before adding new local state"; "Slots are additive
extension points. Use them before copying a component"), docs/stack.md (Storybook
owns the component workshop; TanStack Table owns columns/sort/filter via the
ListView/BoardView bindings).

- id: storybook-001
  loc: packages/storybook/src/stories/ListView.stories.tsx:91
  category: wrong-level-placement
  severity: high
  rule: docs/frontend/guidelines.md "Use shared page, view, form, table, widget, and shell primitives before adding new local state"; AGENTS.md Constitution "Delegate to the library that owns the concern … do not rebuild it"
  finding: Story titled "Views/ListView" never renders the real exported ListView; it re-implements a DataToolbar + Table + column-visibility composition by hand instead of previewing the owning view (FormView.stories already shows the precedent of mocking the data layer to render the real view).
  fix: Render the public ListView with a mocked SDK list result (as FormView.stories mocks the fetch), so the workshop previews the component it is named for; delete the hand-rolled table.
  status: fixed
- id: storybook-002
  loc: packages/storybook/.storybook/preview.tsx:124
  category: docs-code-drift
  severity: high
  rule: AGENTS.md "Make extension mechanical: … deterministic order"; AGENTS.md "Verify before claiming done. Drift is a bug"; docs/guidelines.md "Name So Code Can Be Found"
  finding: storySort.order lists ["Tokens","Primitives","Chrome","Widgets","Toolbars","Shell","Scenes","Reference"], but 4 of those 8 categories have zero stories (Tokens, Toolbars, Scenes, Reference) and 6 categories that actually exist are missing from the order (Fragments=26 stories, Page=8, Layouts=4, Views=2, Forms=1, Feedback=1) — the sidebar order is stale and the title taxonomy is undeclared.
  fix: Reconcile storySort.order with the actual top-level title prefixes (one declared, deterministic taxonomy), dropping dead category names and adding the real ones.
  status: fixed
- id: storybook-003
  loc: packages/storybook/src/stories/FormView.stories.tsx:136
  category: code-bigger-not-smarter
  severity: medium
  rule: docs/guidelines.md "The code is bigger instead of smarter" (copy-pasted variations that differ by only a value); AGENTS.md DRY "Same shape in three places: extract the smallest boring primitive"
  finding: The read-only variant is built by hand-cloning all 10 editable fields into 10 `readOnly*Field = { ...field, readOnly: true }` consts plus a parallel readOnlyGroups literal (lines 136-205) — a mechanical copy of the editable set that differs only by `readOnly: true`.
  fix: Derive the read-only fields/groups from the editable ones with a single map that sets `readOnly: true`, deleting the 14 hand-cloned declarations.
  status: fixed
- id: storybook-004
  loc: packages/storybook/src/stories/ListView.stories.tsx:59
  category: unearned-code
  severity: medium
  rule: AGENTS.md Constitution "Prefer deletion to abstraction"; docs/guidelines.md red flag "Repeating coding work unnecessarily" / dead defensiveness
  finding: A full UseResourceListResult literal (`list`, lines 59-76) is fabricated with rows, setPage, firstPage/nextPage/prevPage/lastPage, refetch, fetching, error, pageInfo, pageCount — but only 5 scalar fields (total/page/pageSize/hasNext/hasPrev) are read into the pager; the 12 callback/state members are dead scaffolding that exist only to satisfy the type the story never uses as a list result.
  fix: Once the story renders the real ListView (storybook-001) the literal disappears; until then, pass a plain pager-shaped object instead of casting a full UseResourceListResult.
  status: fixed
