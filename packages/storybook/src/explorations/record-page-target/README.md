# 2026-10-20 Record page target

This dated exploration renders the architect's **2026-10-20 four-region ruling**:
Navigator (16rem), Subject, Context aside (26rem, initially open), and Tools edge
(36px). The source document sits beside the bill form. The file and knowledge
page bodies are their content. Nexus switches from conversations to a message in
the same subject, with context scoped to the selected sender.

The ruling is the model in
`../.work/plans/briefs/2026-10-20-record-page-target-exploration.md` relative to the
framework source slot. This is an exploration, not a shipped owner or API. Delete
each page's stories when its real page lands; replace the remaining drawing with
the corresponding owners. The [authority rule](../../../../../docs/frontend/guidelines.md#component-ontology)
governs that transition.

## Fixtures and appearance

`fixtures.ts` holds the AP identities and record values. The audit's bill
SYN-UBL-9311 totals €307.14, has two September lines, and was issued September 26
with an October 15 due date. Its file is `v6-cloudflare-9311-notax.xml` (5.8 KB).
The printed buyer mismatch comes from audit 02. The knowledge body and the
October purchase-order reference come from audit 10 and the earlier exploration.
The bill audit is posted; this target intentionally depicts the earlier waiting
review state required by the ruling. Surrounding activity, access, and decision
choices are illustrative fixtures, not a fresh database snapshot.

Nexus sender names and aggregate counts come from audit 06. Taylor's first two
subject lines are visible there; other message text and the remaining
conversation are illustrative. The XML download is an abbreviated fixture,
not the original 5.8 KB source. The preview preserves the audited business facts.

The existing `themeId` and `colorScheme` toolbar globals supply Stock/Aurora and
light/dark through Storybook's AppearanceProvider. No private palette or theme
provider is introduced. The canvas targets the full 1600×1000 viewport; its four
regions exclude application navigation that is outside this page-shape ruling.

These are isolated page snapshots. Filters, lenses, tabs, the run drill-in,
Follow, discussion, the first Nexus conversation, and tool drawers have local
interaction wiring. Other navigator records and creation controls are visual
fixtures; they do not route to invented records. Lifecycle actions show a preview
notice. No action changes the AP stack.

## Owner map and gaps

Every dashed boundary has a `spec` caption; its owner is named by `data-spec` and
the caption's accessible label. These drawings are
temporary specifications for the listed owners, composed with real primitives.
No shipped component is modified.

| Drawing | Pages | Owner it specifies; why the shipped API cannot render it |
|---|---|---|
| Four-region frame | All; labelled in Anatomy | Future `RecordPage` page shape. There is no shipped unified owner. |
| RecordHeader | All | `RecordHeader` / FormView record header: the shipped heading role is 18px (FormView has its own header), with no title-row statusbar and waits slot. The target uses the shipped 22px `display` recipe and `StatusbarSteps`. |
| CommandBand | All | FormView / record command-band region: no shared API owns this exact 40px menu-first lifecycle/control/access anatomy across readers and forms. Buttons, tooltips, avatars, menus and popovers are shipped. |
| Record pager | All | `RecordPager` has the current/total label, but places both arrows after it and exposes no order or tooltip slot. Collection `Pager` also places arrows after its range label. The target specifies arrow-label-arrow with tooltips, drawn from shipped buttons. |
| List-with-views / lens wiring | Bill, Nexus | `ScopedExplorerPane`: its controller is scoped-tree-specific. The drawing composes one PaneToolbarHost/PaneToolbar with native ScopeChips on a second line inside the same outlined head, fitting the 16rem navigator. PaneToolbar's native band is fixed at 32px and cannot contain both full selector and scopes with three controls on one line at this width. File/vault trees use the shipped ScopedExplorerPane and TreeView. |
| Fixed-left aside strip | All | `Chatter`: each tab has unconditional `flex-1` and no strip layout prop. This study uses its native ChatterProvider/navigation state, with shipped Tabs and PaneToolbarHost for the drawn head/panel composition. |
| Timeline scope menu | Timeline states | `TimelineRail`: it emits three chips and a native select, with no head render slot. The study draws the requested two chips + nine-kind menu, search and overflow, then composes shipped PaneLabel and PaneRow for entries. |
| Compact run drill-in | Bill run; reachable from other timelines | Workflow run pane / `StepList`: StepList owns a different step anatomy. The drawing uses PaneRow for compact steps, an Alert only for the buyer warning, and one Stop control. |
| DocumentPane | Bill | Future document region beside the form, with its own file toolbar. The preview resolution itself uses shipped PreviewPane. |
| UBL preview | Bill, File | PreviewPane's future UBL provider: the shipped built-ins display XML as source text. A fixture-only provider draws a readable e-invoice with audited values through the native preview contribution seam. |
| Compact tools edge | All | `DrawerRail`: fixed-position, vertically rotated text tabs, no compact/icon-only/inline mode. This drawing uses shipped tooltips, buttons and Drawer; Agents and Logs open here. Node inspection belongs here, not in the aside. |

Field and Group declarations are parsed by `parsePageGroups`; `FormGrid`,
`FieldRow`, Chip marks, FormView.SectionHeading, Tabs and EditableLines supply
the form anatomy. Invoice amounts use the shipped money addon's `moneyWidget`
and `formatMoney`, loaded through the same explicit Vite source seam as Decisions.
`RelatedPane` receives groups through `related#groups`,
including Files/Attach, Pages/Bind, Runs, Sessions, Conversations, Sources/Odoo
and Shared with. MessageFeed, MessageRow, Avatar, Textarea and MessageComposer
render discussion and the message reader; absolute times use MessageRow's meta
slot rather than its relative-time slot. All status chips use `statusTone`.

The run mounts the **real Decisions `DecisionCard`**, loaded from its shipped
source by Vite's explicit glob. That uses the composed Storybook's existing
GraphQL alias without adding a dependency or importing project-generated schema
into the Storybook TypeScript graph. The existing RuntimeFixture supplies a
local mutation responder. Confirm and Stop only change this isolated preview.

The target's Related⁵ tab count is the architect's supplied annotation, not the
sum of the expanded Related example (nine linked objects across seven groups).
The owner lane must settle count semantics before production; this drawing does
not change RelatedPane's existing sum-of-readable-edges policy.

## Stories

All IDs have the prefix `explorations-2026-10-20-record-page-target--`:

1. `vendor-bill`
2. `vendor-bill-run-open`
3. `vendor-bill-related`
4. `vendor-bill-discussion`
5. `file`
6. `file-related`
7. `knowledge-page`
8. `knowledge-page-related`
9. `nexus-inbox`
10. `nexus-inbox-message`
11. `anatomy`

The catalog scans `src/stories`, not `src/explorations`; these files therefore
add no Library entries. The composition report derives its owners from the
catalog and shipped source graph, not this exploration.

## Verification

From `/Users/alexis/Work/phrogg/phrogg-ap-angee`:

```sh
pnpm --config.verify-deps-before-run=false --filter @angee/storybook exec tsc --noEmit -p .
pnpm --config.verify-deps-before-run=false --filter @angee/ui exec vitest run src/catalog.test.ts
pnpm --config.verify-deps-before-run=false --filter @angee/storybook run catalog:check
pnpm --config.verify-deps-before-run=false --filter @angee/storybook run composition:check
```

Local results on 2026-10-20:

| Check | Result |
|---|---|
| Storybook TypeScript | Passed. |
| UI catalog test | Failed: `@angee/ui:TREE_INSERTION_ID: missing or invalid @kind/@owner`. The other test passed. |
| Storybook catalog check | Failed: ten tests stop at setup on the same missing annotation. |
| Composition check | Passed: 2,474 nodes / 9,379 edges; valid and sorted. |
| Live index and Vite | All 11 requested IDs present; 18/18 exploration/addon/schema modules return HTTP 200. This verifies resolution, not rendering. |
| Authority exclusion | Neither the checked-in UI catalog nor the composition report contains this exploration. The generator scans `src/stories` only. |
| Primitive-drift scan | No raw table/grid implementation, manual pagination, or handwritten GraphQL operation hits in the new exploration's TS/TSX files. |
| Browser / screenshots | Blocked before rendering: local Chromium fails macOS Mach-port registration with permission denied; the Playwright connector requires approval unavailable in this session; the computer-use surface exposes no browser. **0/44 screenshots captured. Pixel fidelity and interactions remain unverified.** |

The catalog defect belongs to
[`TreeView.tsx`](../../../../ui/src/views/tree/TreeView.tsx): the exported
`TREE_INSERTION_ID` needs its owning annotation and the owner lane's generated
catalog reconciliation. This lane cannot repair it because shipped UI edits are
explicitly excluded. The exploration remains incomplete until that check and
browser verification pass.

Screenshots and browser evidence belong in
[the screenshot index](../../../../../.playwright-mcp/record-page-target/index.md),
which lists all 44 pending captures in page order and links the audit references.
The existing Playwright dependency is wired by the capture script there.
[Source verification](../../../../../.playwright-mcp/record-page-target/source-verification.json)
records the live IDs, HTTP checks, and authority exclusion. These are local
checks, not new CI coverage.
