// @vitest-environment happy-dom
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, test } from "vitest";

import { ActionRejected, DuplicateRisk, QueryError, ReadOnly, Recovery, Redacted, Runs, RunStory, Unavailable, Waiting, type RunRequest } from "./RunPage.stories";
import { runFixture, stepRunFixture } from "./testing";

beforeAll(() => { Element.prototype.getAnimations ??= () => []; });
afterEach(cleanup);

async function openAction(label: string) {
  fireEvent.click((await screen.findAllByRole("button", { name: label }))[0]!);
  return screen.findByRole("dialog");
}

describe("workflow runs with native router, providers and generated operations", () => {
  test("opens a run from the list and resolves its subject and artifact through metadata", async () => {
    render(Runs.render());
    expect(await screen.findByText("Review notes")).toBeTruthy();
    const runLink = screen.getAllByRole("link").find((link) => link.getAttribute("href") === "/workflows/runs/wfr_review");
    expect(runLink).toBeTruthy();
    fireEvent.click(runLink!);
    expect(await screen.findByText("The operation did not finish.")).toBeTruthy();
    expect(screen.getByText("TimeoutError: operation expired")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Retained note" }).getAttribute("href")).toBe("/notes/nte_7");
    expect(screen.queryByRole("textbox")).toBeNull();
  });

  test("shows an operator wait reason and retained attempt evidence", async () => {
    render(Waiting.render());
    expect(await screen.findByText("Dispatch attempts exhausted.")).toBeTruthy();
    expect(screen.getByText("The operation did not finish.")).toBeTruthy();
  });

  test("shows the cancel result exactly as returned by the execution owner", async () => {
    render(Waiting.render());
    const dialog = await openAction("Cancel run");
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel run" }));
    expect(await screen.findByText("Open work canceled; the retained run is unchanged.")).toBeTruthy();
    expect(within(screen.getByRole("region", { name: "Notifications" })).getByText("Open work canceled; the retained run is unchanged.")).toBeTruthy();
    expect(screen.getAllByText("Open work canceled; the retained run is unchanged.")).toHaveLength(1);
  });

  test("reprocess links to the replacement run returned by the mutation", async () => {
    render(Recovery.render());
    const dialog = await openAction("Reprocess run");
    fireEvent.click(within(dialog).getByRole("button", { name: "Reprocess run" }));
    expect(await screen.findByText("Run reprocessed.")).toBeTruthy();
    await waitFor(() => expect(screen.getAllByRole("link").some((link) => link.getAttribute("href") === "/workflows/runs/wfr_replacement")).toBe(true));
    expect(within(screen.getByRole("region", { name: "Notifications" })).queryByText("Run reprocessed.")).toBeNull();
  });

  test("retries a step and preserves the backend result message", async () => {
    render(Recovery.render());
    const dialog = await openAction("Retry step");
    fireEvent.click(within(dialog).getByRole("button", { name: "Retry step" }));
    expect(await screen.findByText("Step retried; the run is waiting.")).toBeTruthy();
    expect(within(screen.getByRole("region", { name: "Notifications" })).getByText("Step retried; the run is waiting.")).toBeTruthy();
    expect(screen.getAllByText("Step retried; the run is waiting.")).toHaveLength(1);
  });

  test("requires an accessible acknowledgement before accepting a possible duplicate", async () => {
    const requests: RunRequest[] = [];
    render(<RunStory steps={[stepRunFixture({ requires_duplicate_acknowledgement: true })]} onRequest={(request) => requests.push(request)} />);
    const label = "Retry accepting a possible duplicate";
    const dialog = await openAction(label);
    const acknowledgement = () => within(dialog).getByRole("checkbox", { name: "I understand this retry may repeat an external effect." });
    fireEvent.click(within(dialog).getByRole("button", { name: label }));
    await waitFor(() => expect(acknowledgement().getAttribute("aria-invalid")).toBe("true"));
    const error = await within(dialog).findByText("Acknowledge the possible duplicate before retrying.");
    const errorIds = acknowledgement().getAttribute("aria-describedby")?.split(" ") ?? [];
    expect(errorIds.some((id) => document.getElementById(id)?.contains(error))).toBe(true);
    expect(within(dialog).queryByRole("alert")).toBeNull();
    expect(requests.some(({ query }) => query.includes("mutation"))).toBe(false);
    expect(screen.queryByText("Step retried with duplicate risk acknowledged.")).toBeNull();
    fireEvent.click(acknowledgement());
    fireEvent.click(within(dialog).getByRole("button", { name: label }));
    expect(await screen.findByText("Step retried with duplicate risk acknowledged.")).toBeTruthy();
    expect(requests.some(({ query, variables }) => query.includes("retry_step_accepting_duplicate") && variables.id === "wsr_inspect")).toBe(true);
  });

  test("shows a manager rejection in the action dialog without claiming success", async () => {
    render(ActionRejected.render());
    const dialog = await openAction("Retry step");
    fireEvent.click(within(dialog).getByRole("button", { name: "Retry step" }));
    expect(await within(dialog).findByText("This step cannot be retried in place.")).toBeTruthy();
    expect(screen.queryByText("Step retried; the run is waiting.")).toBeNull();
  });

  test("renders only diagnostics delivered by the backend", async () => {
    render(Redacted.render());
    await screen.findByRole("heading", { name: "Attempt 1" });
    expect(screen.queryByText("TimeoutError: operation expired")).toBeNull();
    expect(screen.queryByText("The operation did not finish.")).toBeNull();
  });

  test("renders an unavailable run without operator controls", async () => {
    render(Unavailable.render());
    expect(await screen.findByText("This run was not found or is not readable.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Cancel run" })).toBeNull();
  });

  test("keeps request failure separate from an unavailable run", async () => {
    render(QueryError.render());
    const alert = await screen.findByRole("alert", {}, { timeout: 10000 });
    expect(within(alert).getByText("Could not load this run.")).toBeTruthy();
    expect(screen.queryByText("This run was not found or is not readable.")).toBeNull();
    expect(screen.queryByRole("button", { name: "Cancel run" })).toBeNull();
  }, 15000);

  test("uses backend action facts and mounts one dialog only when an action is selected", async () => {
    render(Recovery.render());
    await screen.findByRole("button", { name: "Retry step" });
    expect(screen.queryByRole("button", { name: "Cancel run" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Retry accepting a possible duplicate" })).toBeNull();
    expect(screen.queryByRole("dialog")).toBeNull();
    await openAction("Retry step");
    expect(screen.getAllByRole("dialog")).toHaveLength(1);
  });

  test("returns focus to the chosen operator action after dismissing the lazy dialog", async () => {
    render(Recovery.render());
    const trigger = await screen.findByRole("button", { name: "Retry step" });
    trigger.focus();
    fireEvent.click(trigger);
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "Close" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    await waitFor(() => expect(document.activeElement).toBe(trigger));
  });

  test("offers only the acknowledgement retry when required and hides unsupported actions", async () => {
    const view = render(DuplicateRisk.render());
    await screen.findByRole("button", { name: "Retry accepting a possible duplicate" });
    expect(screen.queryByRole("button", { name: "Retry step" })).toBeNull();
    view.unmount();
    render(ReadOnly.render());
    await screen.findByRole("heading", { name: "inspect", level: 3 });
    for (const name of ["Cancel run", "Reprocess run", "Retry step", "Retry accepting a possible duplicate"]) {
      expect(screen.queryByRole("button", { name })).toBeNull();
    }
  });

  test("renders complete retained errors as data and displays the run actor name", async () => {
    const error = "Retained details: " + "all evidence remains visible. ".repeat(50);
    render(<RunStory run={runFixture({ error })} />);
    expect(await screen.findByText(error.trim())).toBeTruthy();
    expect(await screen.findByText("The operation did not finish.")).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByText("Operator")).toBeTruthy();
    expect(screen.queryByText("usr_operator")).toBeNull();
  });

  test("keeps backend step order and semantic headings, including mapped index zero", async () => {
    const steps = [stepRunFixture({ id: "wsr_z", node_key: "z-first", is_mapped: true }),
      stepRunFixture({ id: "wsr_a", node_key: "a-second", rank: 1, can_retry: false, attempts: [], artifacts: [] })];
    render(<RunStory steps={steps} />);
    await screen.findByRole("heading", { name: "a-second", level: 3 });
    expect(screen.getByRole("heading", { name: "Step runs", level: 2 })).toBeTruthy();
    const headings = screen.getAllByRole("heading", { level: 3 });
    expect(headings.map((heading) => heading.textContent)).toEqual(["z-first [0]", "a-second"]);
    expect(headings[0]?.closest("ol > li")).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Attempt 1", level: 4 })).toBeTruthy();
  });

  test("paginates run-scoped step reads and loads details for visible rows only", async () => {
    const requests: RunRequest[] = [];
    const steps = Array.from({ length: 11 }, (_, index) => stepRunFixture({
      id: `wsr_${index}`, node_key: "mapped", is_mapped: true, map_index: index, rank: 0,
      attempts: [], artifacts: [], can_retry: false,
    }));
    render(<RunStory steps={steps} onRequest={(request) => requests.push(request)} />);
    await screen.findByRole("heading", { name: "mapped [9]", level: 3 });
    expect(screen.queryByRole("heading", { name: "mapped [10]" })).toBeNull();
    expect(requests.some(({ query, variables }) => query.includes("steprun_by_pk") && variables.id === "wsr_10")).toBe(false);
    const listRequest = requests.find(({ query }) => /\bsteprun\s*\(/.test(query));
    expect(listRequest?.variables).toMatchObject({ where: { _and: [{ run: { _eq: "wfr_review" } }] }, limit: 10, offset: 0,
      order_by: { rank: "asc", map_index: "asc" } });
    fireEvent.click(screen.getByRole("button", { name: "Next page" }));
    await screen.findByRole("heading", { name: "mapped [10]", level: 3 });
    expect(screen.queryByRole("heading", { name: "mapped [0]" })).toBeNull();
    expect(requests.some(({ query, variables }) => /\bsteprun\s*\(/.test(query) && variables.offset === 10)).toBe(true);
  });
});
