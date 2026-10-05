// @vitest-environment happy-dom
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeAll, expect, test, vi } from "vitest";
import { decisionFixture } from "@angee/decisions/testing";
import { TimelineStory, type TimelineRequest } from "./RecordTimeline.stories";
import { timelineFixture } from "./timeline-testing";

beforeAll(() => {
  Element.prototype.getAnimations ??= () => [];
  Element.prototype.scrollIntoView ??= vi.fn();
});
afterEach(() => cleanup());

test("a completed run shows its outcome, workflow name, local time and run link", async () => {
  render(<TimelineStory state="clean" />);
  expect(await screen.findByText("Plan complete")).toBeTruthy();
  expect(screen.getByRole("heading", { name: /Record review/ })).toBeTruthy();
  expect(screen.getByText("Open run")).toBeTruthy();
  expect(document.querySelector('time[datetime="2026-10-03T10:00:00Z"]')).toBeTruthy();
});

test.each([
  ["decision", "Waiting for decisions"], ["clean", "Plan complete"], ["error", "Stopped on an error"],
  ["run", "Waiting for another run"], ["stopped", "Withdrawn: stopped by River"],
] as const)("renders the %s timeline state", async (state, label) => {
  render(<TimelineStory state={state} />);
  expect((await screen.findAllByText(label)).length).toBeGreaterThan(0);
  if (state === "error") expect(screen.getByRole("button", { name: "Retry" })).toBeTruthy();
  if (state === "run") expect(screen.getByText("Waiting for Related record check")).toBeTruthy();
});

test("answers inline with the observed revision and refreshes the stepper", async () => {
  const requests: TimelineRequest[] = [];
  render(<TimelineStory onRequest={(request) => requests.push(request)} />);
  fireEvent.click(await screen.findByRole("radio", { name: /Use proposed name/ }));
  fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
  await screen.findByText("Plan complete");
  expect(requests.find(({ query }) => query.includes("decide("))?.variables).toMatchObject({
    id: "dcn_review", revision: 3, chosen: ["accept"],
  });
  expect(screen.queryByRole("radio")).toBeNull();
  expect(screen.getByText("Chose: Use proposed name")).toBeTruthy();
});

test("only records with runs select the timeline initially", async () => {
  const view = render(<TimelineStory state="empty" />);
  await waitFor(() => expect(screen.getByTestId("timeline-right").getAttribute("data-state")).toBe("false:agents.chat"));
  view.unmount();
  render(<TimelineStory />);
  await screen.findByText("Started manually by River");
  expect((await screen.findByTestId("timeline-right")).getAttribute("data-state")).toBe("false:workflows.timeline");
});

test("a first visit opens a collapsed pane only when a decision is waiting", async () => {
  const view = render(<TimelineStory collapsed />);
  await waitFor(() => expect(screen.getByTestId("timeline-right").getAttribute("data-state")).toBe("false:workflows.timeline"));
  view.unmount();
  render(<TimelineStory collapsed state="clean" />);
  await waitFor(() => expect(screen.getByTestId("timeline-right").getAttribute("data-state")).toBe("true:workflows.timeline"));
});

test("answered cards do not nest list items outside a list", async () => {
  const { container } = render(<TimelineStory state="clean" />);
  await screen.findByText("Chose: Use proposed name");
  expect(screen.queryByText("Use proposed name", { exact: true })).toBeNull();
  expect(screen.getByText("Review the record")).toBeTruthy();
  for (const item of container.querySelectorAll("li")) {
    expect(["UL", "OL"]).toContain(item.parentElement?.tagName);
  }
});

test("a retry with possible duplicate effects uses the shared acknowledgement form", async () => {
  const requests: TimelineRequest[] = [];
  render(<TimelineStory state="error" duplicateRisk onRequest={(request) => requests.push(request)} />);
  const label = "Retry accepting a possible duplicate";
  fireEvent.click(await screen.findByRole("button", { name: label }));
  const dialog = await screen.findByRole("dialog");
  fireEvent.click(within(dialog).getByRole("button", { name: label }));
  const checkbox = within(dialog).getByRole("checkbox", { name: "I understand this retry may repeat an external effect." });
  await waitFor(() => expect(checkbox.getAttribute("aria-invalid")).toBe("true"));
  expect(requests.some(({ query }) => query.includes("retry_step_accepting_duplicate("))).toBe(false);
  fireEvent.click(checkbox);
  fireEvent.click(within(dialog).getByRole("button", { name: label }));
  await screen.findByText("Waiting for decisions");
  expect(requests.find(({ query }) => query.includes("retry_step_accepting_duplicate("))?.variables.id).toBe("wsr_review");
});

test("stop confirms through the shared action before withdrawing questions", async () => {
  const requests: TimelineRequest[] = [];
  render(<TimelineStory onRequest={(request) => requests.push(request)} />);
  const card = await screen.findByRole("region", { name: "Confirm the name" });
  expect((within(card).getByRole("button", { name: "Stop and do it manually" }) as HTMLButtonElement).disabled).toBe(false);
  fireEvent.click(within(card).getByRole("button", { name: "Stop and do it manually" }));
  const dialog = await screen.findByRole("alertdialog");
  expect(requests.some(({ query }) => query.includes("cancel_workflow_run("))).toBe(false);
  fireEvent.click(within(dialog).getByRole("button", { name: "Stop and do it manually" }));
  await screen.findByText("Withdrawn: stopped by River");
  expect(requests.find(({ query }) => query.includes("cancel_workflow_run("))?.variables.id).toBe("wfr_review");
  expect(screen.queryByText("May also: Check a related record")).toBeNull();
  expect(screen.queryByRole("button", { name: "Stop and do it manually" })).toBeNull();
});

test("answering refreshes the concerned form without a socket", async () => {
  const requests: TimelineRequest[] = [];
  render(<TimelineStory onRequest={(request) => requests.push(request)} />);
  fireEvent.click(await screen.findByRole("radio", { name: /Use proposed name/ }));
  fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
  await waitFor(() => expect((screen.getByRole("textbox", { name: "Name" }) as HTMLInputElement).value,
    JSON.stringify(requests.map(({ query }) => query.slice(0, 100)))).toBe("Reviewed notes"));
});

test("routine history folds while waiting and the record reference opens a peek", async () => {
  render(<TimelineStory />);
  const fold = await screen.findByRole("button", { name: "Show 7 steps done" });
  fireEvent.click(fold);
  expect(screen.getByText("Receive record")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: /Review notes/ }));
  expect(await screen.findByRole("navigation", { name: "Related records" })).toBeTruthy();
  expect(screen.getByTestId("timeline-right").getAttribute("data-state")).toBe("false:records");
});

test("form marks reveal and highlight the inline card through the right host", async () => {
  render(<TimelineStory />);
  await screen.findByRole("button", { name: "Unconfirmed: Name" });
  expect(screen.getByRole("button", { name: "Unconfirmed: Name" }).closest("label")).toBeNull();
  const current = screen.getByText("Review the record").closest("li")!;
  expect(current.getAttribute("aria-current")).toBe("step");
  expect(current.textContent).toContain("Current step");
  expect(screen.getByTestId("timeline-right").getAttribute("data-state")).toBe("false:workflows.timeline");
  expect(screen.getByRole("region", { name: "Confirm the name" }).className).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Toggle timeline" }));
  expect(screen.queryByRole("region", { name: "Confirm the name" })).toBeNull();
  fireEvent.click(await screen.findByRole("button", { name: "Unconfirmed: Name" }));
  await waitFor(() => expect(document.querySelector('[data-decision="dcn_review"]')?.className).toContain("ring-2"));
});

test.each(["error", "unknown"] as const)("the %s run has its own explicit status and a neutral fallback", async (state) => {
  render(<TimelineStory state={state} />);
  if (state === "error") {
    expect(await screen.findByText("A parallel branch failed.")).toBeTruthy();
    expect(screen.queryByText("Running", { exact: true })).toBeNull();
  } else expect(await screen.findByText("Unknown status")).toBeTruthy();
});

test("one component publishes to the left host", async () => {
  render(<TimelineStory side="left" />);
  await screen.findByText("Waiting for decisions");
  expect(screen.getByTestId("timeline-left").textContent).toContain("Confirm the name");
  expect(screen.queryByTestId("timeline-right")).toBeNull();
});

test("selection groups open questions and held runs by record or question", async () => {
  render(<TimelineStory state="set" />);
  await screen.findByText("2 records · 1 open decision · 1 run waiting or stopped");
  expect(screen.getAllByRole("region", { name: "Confirm the name" })).toHaveLength(2);
  fireEvent.click(screen.getByRole("button", { name: "By question" }));
  expect(screen.getAllByRole("region", { name: "Confirm the name" })).toHaveLength(1);
  expect(screen.getAllByText("Confirm the name").length).toBeGreaterThan(0);
  expect(screen.getByRole("button", { name: "Retry" })).toBeTruthy();
});

test("question grouping distinguishes kinds that share a label", async () => {
  const data = timelineFixture("set");
  data.records[0]!.decisions.push(decisionFixture({ id: "dcn_other", kind: "other_kind", kind_label: "Confirm the name" }));
  data.open_decision_count = 2;
  render(<TimelineStory state="set" data={data} />);
  fireEvent.click(await screen.findByRole("button", { name: "By question" }));
  expect(screen.getAllByRole("heading", { level: 3, name: /Confirm the name/ })).toHaveLength(2);
});

test("an unknown note tone renders with the information treatment", async () => {
  const data = timelineFixture();
  data.records[0]!.runs[0]!.graph.nodes[7]!.step_run!.notes.push({ message: "A new note tone", tone: "future_tone" });
  render(<TimelineStory data={data} />);
  const note = await screen.findByText("A new note tone");
  expect(note.closest('[role="status"]')?.classList.contains("bg-info-soft")).toBe(true);
});
