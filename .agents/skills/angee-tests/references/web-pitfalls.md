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

Run the package vitest suite — not just `tsc` and a story render, which miss
stale assertion drift. When verifying data-bound views, wait for the async query
to load before asserting.
