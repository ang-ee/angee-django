// @vitest-environment happy-dom
// @vitest-environment-options {"settings":{"disableIframePageLoading":true}}
// (The Library story embeds stories as iframes of the running workshop, which no server answers here.)
// The workshop package's own stories (`src/stories`), composed as the workshop composes them: the
// preview's own decorator and parameters (`setProjectAnnotations`, `composeStories`). Each story module
// of this package imports, so one that throws fails with its path; each of its stories renders without
// the warning of a schema that names no resources; and the stories a browser pass reads show what they
// claim. The fragments' and products' stories the workshop also loads need generated documents this
// package's tests do not have, so this check does not import them.
import { composeStories, setProjectAnnotations } from "@storybook/react-vite";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ComponentType } from "react";
import { afterEach, expect, test, vi } from "vitest";
import preview from "../../.storybook/preview";
import * as FormViewStories from "./FormView.stories";
import * as ListViewStories from "./ListView.stories";

setProjectAnnotations([preview]);
afterEach(() => { vi.restoreAllMocks(); });

/** What a resource surface warns when the schema's metadata names no resource for its model. */
const METADATA_WARNING = "model metadata lookup";
/** What a story that crashed while rendering, or read a root its schema does not answer, leaves in the console. */
const STORY_FAILURE = /no handler answers|The above error occurred/;
/** How long a record story is still watched once it shows its record: a warning it logs that late fails its own test. */
const LATE_WARNING_WINDOW_MS = 500;

/** The warnings and errors a test's story logs, collected for the whole test. */
function consoleMessages(): { warnings: string[]; errors: string[] } {
  const said = { warnings: [] as string[], errors: [] as string[] };
  vi.spyOn(console, "warn").mockImplementation((...args: unknown[]) => { said.warnings.push(args.map(String).join(" ")); });
  vi.spyOn(console, "error").mockImplementation((...args: unknown[]) => { said.errors.push(args.map(String).join(" ")); });
  return said;
}

/** Until the document stops changing for two tasks in a row (at most `limit` tasks): the story's reads have answered. */
async function settled(limit = 50): Promise<void> {
  for (let task = 0, quiet = 0; task < limit && quiet < 2; task += 1) {
    let changed = false;
    const observer = new MutationObserver(() => { changed = true; });
    observer.observe(document.body, { subtree: true, childList: true, characterData: true, attributes: true });
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
    observer.disconnect();
    quiet = changed ? 0 : quiet + 1;
  }
}

// Each module is imported before the tests run, so one that throws is reported by its path.
const storyModules = import.meta.glob("./**/*.stories.tsx");
const paths = Object.keys(storyModules).sort();
const loaded = new Map(await Promise.all(paths.map(async (path) => [path, await storyModules[path]!().then(
  (module) => ({ module: module as Parameters<typeof composeStories>[0] }),
  (error: unknown) => ({ error }),
)] as const)));

test.each(paths)("%s imports", (path) => {
  const result = loaded.get(path)!;
  if ("error" in result) throw result.error;
});

/** The stories a browser pass reads, each checked by its own test below and so not rendered again here. */
const RECORD_STORIES = ["EditMode", "ReadOnlyMode", "GroupedSections", "RetainedPanelActivity", "FullBleedTab", "RecordRail", "RelationWithoutEdit"] as const;
const OWN_TESTS = new Set([
  ...RECORD_STORIES.map((name) => `./FormView.stories.tsx ${name}`), "./ListView.stories.tsx GroupedWithTieBreakers",
  "./ListView.stories.tsx ImplementationKeyNotRegistered", "./FormView.stories.tsx ImplementationKeyNotRegistered",
  "./FormView.stories.tsx ImplementationKeyNotRegisteredCreateOnly",
  "./ListView.stories.tsx PaneWithOrder",
]);
/** The stories not rendered here, each with why. */
const NOT_RENDERED: ReadonlyMap<string, string> = new Map([
  ["./PreviewFileContent.stories.tsx FetchFailed",
    "fetches a missing file from the workshop's own server to show a failed read; here that request would leave the test"],
]);
const stories = paths.flatMap((path) => {
  const result = loaded.get(path)!;
  return "module" in result
    ? Object.entries(composeStories(result.module)).map(([name, Story]) => [`${path} ${name}`, Story as ComponentType] as const)
      .filter(([story]) => !NOT_RENDERED.has(story) && !OWN_TESTS.has(story))
    : [];
});

test.each(stories)("%s renders without a crash, an unanswered read or a model its schema does not name", async (_story, Story) => {
  const said = consoleMessages();
  const view = render(<Story />);
  await settled();
  view.unmount();
  expect(said.warnings.filter((message) => message.includes(METADATA_WARNING))).toEqual([]);
  // A crash under the preview's decorator is caught and logged by React, and a read no handler answers is
  // logged by the wire fake: neither throws here, so both are read from what the story logged.
  expect(said.errors.filter((message) => STORY_FAILURE.test(message))).toEqual([]);
});

const formView = composeStories(FormViewStories);
const TITLE = "Launch readiness review";

test.each(RECORD_STORIES)(
  "Views/FormView / %s shows its record's title, not \"Record unavailable\", and finds its model",
  async (name) => {
    const said = consoleMessages();
    const Story = formView[name];
    render(<Story />);
    // Editable, the title is the field's value; read-only, its text.
    await waitFor(() => expect([...screen.queryAllByDisplayValue(TITLE), ...screen.queryAllByText(TITLE)]).not.toHaveLength(0));
    expect(screen.queryByText("Record unavailable")).toBeNull();
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, LATE_WARNING_WINDOW_MS)); });
    expect(said.warnings.filter((message) => message.includes(METADATA_WARNING))).toEqual([]);
  },
);

test("Views/FormView / Relation Without Edit offers Edit on the reviewer's picker only, and the Edit opens the reviewer", async () => {
  const said = consoleMessages();
  render(<formView.RelationWithoutEdit />);
  const reviewer = await screen.findByRole("combobox", { name: "Reviewer: Grace Hopper" });
  const approver = screen.getByRole("combobox", { name: "Approver: Alan Turing" });
  const [edit, ...more] = await screen.findAllByRole("button", { name: "Edit record" });
  expect(more).toEqual([]);
  // The one Edit sits with the reviewer's picker: after it, before the approver's.
  expect(reviewer.compareDocumentPosition(edit!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(edit!.compareDocumentPosition(approver) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  fireEvent.click(edit!);
  expect(await within(await screen.findByRole("dialog")).findByDisplayValue("Grace Hopper")).toBeTruthy();
  expect(said.errors.filter((message) => message.includes("no handler answers"))).toEqual([]);
});

test("Views/FormView / Relation Without Edit's pickers list the people", async () => {
  const said = consoleMessages();
  render(<formView.RelationWithoutEdit />);
  fireEvent.click(await screen.findByRole("combobox", { name: "Approver: Alan Turing" }));
  expect(await screen.findByRole("option", { name: /Grace Hopper/ })).toBeTruthy();
  expect(said.errors.filter((message) => message.includes("no handler answers"))).toEqual([]);
});

const listView = composeStories(ListViewStories);
const TASKS = /^(Draft the brief|Book the room|Collect quotes|Send invites|Archive notes|Approve budget)$/;

test("Views/ListView / Grouped With Tie-breakers shows on its headers the sort its rows follow, and title breaks the rank ties", async () => {
  const said = consoleMessages();
  render(<listView.GroupedWithTieBreakers />);
  const group = (name: string) => screen.findByRole("button", { name: new RegExp(`^${name}`) });
  const groups = [await group("closed"), await group("open")];
  const sort = (name: string) => screen.getByRole("columnheader", { name: new RegExp(name) }).getAttribute("aria-sort");
  // Status: a group's tasks share it. Rank: the rows of each group follow it. Title: it only breaks ties.
  expect(["Status", "Rank", "Title"].map(sort)).toEqual(["none", "ascending", "none"]);
  for (const button of groups) fireEvent.click(button);
  await waitFor(() => expect(screen.queryAllByText(TASKS).map((cell) => cell.textContent)).toEqual([
    "Archive notes", "Approve budget", "Send invites", "Book the room", "Draft the brief", "Collect quotes",
  ]));
  expect(said.warnings.filter((message) => message.includes(METADATA_WARNING))).toEqual([]);
});

test("Views/ListView / Implementation Key Not Registered says so in the warning tone, beside a registered implementation", async () => {
  const said = consoleMessages();
  render(<listView.ImplementationKeyNotRegistered />);
  expect((await screen.findByText("Not registered")).className).toContain("bg-warning-soft");
  expect(screen.getByText("Local folder")).toBeTruthy();
  expect(said.warnings.filter((message) => message.includes(METADATA_WARNING))).toEqual([]);
  expect(said.errors.filter((message) => STORY_FAILURE.test(message))).toEqual([]);
});

/** An element's accessible description: the text of the ids its `aria-describedby` names. */
const description = (element: Element) => (element.getAttribute("aria-describedby") ?? "").split(" ")
  .map((id) => document.getElementById(id)?.textContent ?? "").join(" ").trim();

test("Views/FormView / Implementation Key Not Registered marks Backend, says it cannot run until another is chosen and offers them", async () => {
  const said = consoleMessages();
  render(<formView.ImplementationKeyNotRegistered />);
  expect(await screen.findByLabelText("Not registered: Backend")).toBeTruthy();
  const control = screen.getByRole("combobox", { name: "Backend" });
  expect(description(control)).toBe("Its implementation cannot run until another is chosen.");
  fireEvent.click(control);
  expect(await screen.findByRole("option", { name: "Local folder" })).toBeTruthy();
  expect(screen.getByRole("option", { name: "S3 bucket" })).toBeTruthy();
  expect(said.warnings.filter((message) => message.includes(METADATA_WARNING))).toEqual([]);
  expect(said.errors.filter((message) => STORY_FAILURE.test(message))).toEqual([]);
});

test("Views/FormView / Implementation Key Not Registered, Create-only offers Backend once more, and only once", async () => {
  const said = consoleMessages();
  render(<formView.ImplementationKeyNotRegisteredCreateOnly />);
  expect(await screen.findByLabelText("Not registered: Backend")).toBeTruthy();
  const control = await screen.findByRole("combobox", { name: "Backend" });
  expect(description(control)).toBe(
    "Its implementation cannot run until another is chosen. Once saved, the choice cannot be changed.");
  fireEvent.click(control);
  fireEvent.click(await screen.findByRole("option", { name: "Local folder" }));
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(screen.queryByRole("combobox", { name: "Backend" })).toBeNull());
  expect(said.warnings.filter((message) => message.includes(METADATA_WARNING))).toEqual([]);
  expect(said.errors.filter((message) => STORY_FAILURE.test(message))).toEqual([]);
});

test("Views/ListView / Pane With Order checks the key the rows follow, offers no key the groups share, and a choice keeps the groups where they are", async () => {
  const said = consoleMessages();
  // Each request the story's schema answers.
  const reads = vi.spyOn(ListViewStories.default.parameters.angeeFixture.schemas.public!, "fetch");
  render(<listView.PaneWithOrder />);
  for (const name of ["closed", "open"]) fireEvent.click(await screen.findByRole("button", { name: new RegExp(`^${name}`) }));
  const sort = (name: string) => screen.getByRole("columnheader", { name: new RegExp(name) }).getAttribute("aria-sort");
  const shown = () => ({
    headers: ["Status", "Rank", "Title"].map(sort),
    groups: screen.getAllByRole("button", { name: /^(closed|open)/ }).map((button) => button.textContent?.replace(/\d+$/, "")),
    rows: screen.queryAllByText(TASKS).map((cell) => cell.textContent),
  });
  const atLoad = { headers: ["none", "ascending", "none"], groups: ["closed", "open"],
    rows: ["Archive notes", "Approve budget", "Send invites", "Book the room", "Draft the brief", "Collect quotes"] };
  await waitFor(() => expect(shown()).toEqual(atLoad));
  // The menu's Order group, opened with the pane toolbar's own More button when it is closed.
  const order = async () => {
    if (!screen.queryByRole("group", { name: "Order" })) fireEvent.click(screen.getByRole("button", { name: "More" }));
    return within(await screen.findByRole("group", { name: "Order" }));
  };
  const items = async () => (await order()).getAllByRole("menuitemradio").map((item) => [item.textContent, item.getAttribute("aria-checked")]);
  const choose = async (name: string) => { fireEvent.click((await order()).getByRole("menuitemradio", { name })); await settled(); };
  expect(await items()).toEqual([
    ["Rank, ascending", "true"], ["Rank, descending", "false"], ["Title, ascending", "false"], ["Title, descending", "false"],
  ]);
  // The item already checked changes nothing: Title still breaks the rank ties. The read count states the
  // intent only: a list grouped in the browser reads nothing when its order changes, so the rows are the check.
  await settled();
  const loaded = reads.mock.calls.length;
  await choose("Rank, ascending");
  expect([shown(), reads.mock.calls.length]).toEqual([atLoad, loaded]);
  await choose("Title, descending");
  const byTitle = { headers: ["none", "none", "descending"], groups: ["closed", "open"],
    rows: ["Send invites", "Archive notes", "Approve budget", "Draft the brief", "Collect quotes", "Book the room"] };
  await waitFor(() => expect(shown()).toEqual(byTitle));
  const chosen = reads.mock.calls.length;
  expect((await items()).find(([, checked]) => checked === "true")?.[0]).toBe("Title, descending");
  await choose("Title, descending");
  expect([shown(), reads.mock.calls.length, (await items()).find(([, checked]) => checked === "true")?.[0]])
    .toEqual([byTitle, chosen, "Title, descending"]);
  expect(said.warnings.filter((message) => message.includes(METADATA_WARNING))).toEqual([]);
  expect(said.errors.filter((message) => STORY_FAILURE.test(message))).toEqual([]);
});
