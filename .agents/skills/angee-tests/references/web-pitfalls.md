# Story and vitest pitfalls

Traps moved from the frontend guide's Pitfalls and Checks sections. Commands and working directories stay in [Frontend packages and addon fragments](../../../../docs/checks.md#frontend-packages-and-addon-fragments); the rendering and story fixture contracts stay in [Rendering and testing](../../../../docs/frontend/guidelines.md#rendering-and-testing).

- **A storybook `meta.args`/`argTypes` is dead only if no story consumes it.** A
  bare `export const X: Story = {}` (or a `render: (args) => …`) AUTO-RENDERS from
  `meta.args` — those args are live; only a file whose every story is a zero-param
  `render: () => …` has dead meta args. Removing them when `meta.component` has a
  required prop breaks `StoryObj<typeof meta>` (it still demands the arg) — type the
  self-rendering stories as bare `StoryObj` (keep `component:` for autodocs). The
  fixture a data-bound story declares (`parameters.angeeFixture`, `RuntimeFixture`,
  `storySchema`, `angeeOwnRuntime`, one provider stack) is the
  [rendering and testing contract](../../../../docs/frontend/guidelines.md#rendering-and-testing).
- **A story whose schema metadata names no resources loads no record.**
  Its `FormView` warns `[Angee] model metadata lookup: Cannot resolve model
  spelling …` and shows "Record unavailable"; the workshop's story check fails on
  that warning when a workshop-package story logs it before its render settles
  (a record story within 500 ms of showing its record), as it fails on a crash
  or an unanswered read, and nothing fails for a fragment's or product's story. Declare the story's resources once,
  `storySchema(operations, { schema, resources })`.
- **A module a story imports never loads a test runner.** In the workshop,
  `vitest` finds Storybook's own expect already set up and throws `Cannot read
  properties of undefined (reading 'customEqualityTesters')` for every story of
  a file whose module graph reaches the runner; the architecture guardrails
  refuse it and name the chain.
  `@angee/refine/testing` and `@angee/ui/testing` are Vitest harnesses; stories
  take `@angee/storybook/testing` and `@angee/refine/testing/wire`.
- **A rendered test waits for the effect itself.** Wait for the save's call or
  the element with the shared waiting limit; never pause a fixed time and then
  assert that something HAS happened, and pass no per-test timeout, a shorter
  one included: on a loaded runner a slow save lands in the next test. A test
  written as an expected failure drops its short wait when it starts passing.
  The limit's owner is [the shared waiting limit](../../../../docs/checks.md#frontend-packages-and-addon-fragments).
- **A hover lands only on a wired trigger.** `createUiTestProviders` removes
  Base UI's tooltip open delay ([its docstring](../../../../packages/ui/src/testing.tsx)),
  but Base UI attaches a trigger's `mouseenter` listener in an effect, and a
  hover fired before that effect runs is lost: no wait recovers it. A route that
  loads after `render` mounts outside `act`, so a `findBy*` can return before the
  trigger is wired. A test that renders through a router and then hovers, or
  otherwise relies on an effect-attached listener, awaits `router.load()` before
  `render`, so the route mounts inside `render`'s `act` and every effect runs
  first: `renderMarks` in [FieldProvenanceMarks.test.tsx](../../../../src/angee/provenance/web/src/FieldProvenanceMarks.test.tsx)
  and `renderRail` in [AppRail.test.tsx](../../../../packages/ui/src/chrome/AppRail.test.tsx).

Run the package vitest suite — not just `tsc` and a story render, which miss
stale assertion drift. When verifying data-bound views, wait for the async query
to load before asserting.

- **A focus assertion is proven by the test DOM only where happy-dom refuses
  focus as a browser does.** happy-dom refuses `focus()` only on an element that
  is already focused, outside the document, carrying its own `disabled`, or under
  `inert`. It computes styles from inline styles and the `<style>` elements it
  holds, but never consults them when it decides focus, and these tests load no
  app stylesheet. A control that can sit inside a disabled fieldset, a
  non-editable editor or a hidden host takes focus in a test and not in a
  browser: assert the target's state when focus was asked (`recordFocusCalls` in
  `@angee/ui/testing`, which refuses what the test DOM can show: a disabled
  fieldset outside its first legend, `[hidden]`, `[inert]`, a closed `details`,
  a computed `display: none`, `visibility: hidden` or `collapse`, an element
  that is not focusable at all), and give the case a browser check or say in the test that
  it has none; a host hidden by an app stylesheet is a browser's to judge. The
  same `focus()` calls, measured in both:

  | Target of `focus()` | happy-dom 20 | Chromium |
  |---|---|---|
  | `<button disabled>` | refuses | refuses |
  | `<input readonly>` | focuses | focuses |
  | `<input>` inside `<fieldset disabled>` | **focuses** | refuses |
  | `<span tabindex="-1" aria-disabled="true">` inside `<fieldset disabled>` | focuses | focuses |
  | `contenteditable="false"` without `tabindex` | **focuses** | refuses |
  | an element under a `display: none` ancestor | **focuses** | refuses |
  | an element outside the document | refuses | refuses |
  | `validity.stepMismatch` of `<input type="number" step="0.01" value="2">` | **true** | false |

  The last row refuses a dialog's float field client-side under happy-dom (its
  step check is `value % step`). A CodeMirror editor's `focus()` raises under
  happy-dom, which dispatches `selectionchange` inside the editor's own
  selection update, where a browser queues it.