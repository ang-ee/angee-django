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
- **A rendered test waits for the effect itself.** Wait for the save's call or
  the element with the shared waiting limit; never pause a fixed time and then
  assert that something HAS happened, and pass no per-test timeout, a shorter
  one included: on a loaded runner a slow save lands in the next test. A test
  written as an expected failure drops its short wait when it starts passing.
  The limit's owner is [the shared waiting limit](../../../../docs/checks.md#frontend-packages-and-addon-fragments).

Run the package vitest suite — not just `tsc` and a story render, which miss
stale assertion drift. When verifying data-bound views, wait for the async query
to load before asserting.
