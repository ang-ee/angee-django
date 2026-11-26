// @vitest-environment happy-dom

import { cleanup, render, screen } from "@testing-library/react";
import { RouterContextProvider, createMemoryHistory, createRootRoute, createRouter } from "@tanstack/react-router";
import { afterEach, expect, test } from "vitest";
import { createUiTestProviders } from "../../testing";
import { ModalsHost, ToastProvider } from "../../feedback";
import { AppRuntimeProvider } from "../../runtime";
import { defaultWidgets } from "../../widgets";
import { ganttResources, ganttRecord, scheduledRecord } from "../../../tests/gantt-fixtures";
import { Column } from "../page";
import { ResourceList } from "./ResourceList";
import { List } from "./List";
import type { ResourceRecordPlacement } from "./resource-list/public";

const { Provider, dataProvider, clearClients } = createUiTestProviders({
  resources: ganttResources,
  queryClientConfig: { defaultOptions: { queries: { retry: false, staleTime: Infinity } } },
});
afterEach(() => { cleanup(); clearClients(); });

async function openRecord(placement: ResourceRecordPlacement): Promise<HTMLElement> {
  dataProvider.getList.mockImplementation(async () => ({ data: [scheduledRecord], total: 1 }));
  const router = createRouter({ routeTree: createRootRoute(), history: createMemoryHistory({ initialEntries: ["/"] }) });
  render(<RouterContextProvider router={router}><Provider><AppRuntimeProvider runtime={{ widgets: defaultWidgets }}><ModalsHost><ToastProvider>
    <ResourceList resource={ganttRecord.modelLabel} scope="local" placement={placement} recordId={scheduledRecord.id} onSelect={() => undefined}
      hideCreate renderRecord={() => <div data-testid="record">Record details</div>}>
      <List><Column field="name" header="Name" /></List>
    </ResourceList>
  </ToastProvider></ModalsHost></AppRuntimeProvider></Provider></RouterContextProvider>);
  return screen.findByTestId("record");
}

/**
 * A class token's utility without its variants (`md:`, `hover:`), whether it is `!important`, and
 * whether a variant aims it at the element's children (`*:`, `**:`) instead of the element.
 */
function utility(token: string): { name: string; important: boolean; children: boolean } {
  let depth = 0;
  let start = 0;
  for (let index = 0; index < token.length; index += 1) {
    if (token[index] === "[") depth += 1;
    else if (token[index] === "]") depth -= 1;
    else if (token[index] === ":" && depth === 0) start = index + 1;
  }
  const name = token.slice(start);
  return {
    name: name.replace(/^!|!$/g, ""),
    important: name.startsWith("!") || name.endsWith("!"),
    children: /(?:^|:)\*\*?:/.test(token.slice(0, start)),
  };
}

// Every utility that sets `overflow`: the overflow scale on either axis, an arbitrary property, and
// the text utilities that clip.
const OVERFLOW_UTILITY = /^(?:overflow(?:-[xy])?-|\[overflow(?:-[xy])?:|truncate$|line-clamp-)/;
const overflowClasses = (element: Element) => [...element.classList].map(utility).filter(({ name }) => OVERFLOW_UTILITY.test(name));
const inlineOverflow = (element: HTMLElement) => element.style.overflow || element.style.overflowX || element.style.overflowY
  || (/(?:^|;)\s*overflow(?:-[xy])?\s*:/.test(element.getAttribute("style") ?? "") ? "set" : "");

/** The record's nearest ancestor with any overflow rule, a class or an inline style. */
function nearestOverflowRule(record: HTMLElement): HTMLElement {
  for (let element = record.parentElement; element; element = element.parentElement) {
    if (overflowClasses(element).length > 0 || inlineOverflow(element)) return element;
  }
  throw new Error("No ancestor of the record carries an overflow rule.");
}

// What a test without a layout engine can hold: which element is the open record's nearest overflow
// rule, what that rule is, and the classes that size it and the list's root to their room. That
// the heights resolve and the record's last line is reachable is a browser's to show, in the
// Views/ResourceList Split and Inline stories; nothing measures them automatically. A class check
// also cannot see a clip inside the record itself, a stylesheet rule, `contain` or `clip-path`.
test("an open record's nearest overflow rule is its placement's container: the list's record sheet inline, the content pane of the list's own workbench in split", async () => {
  const inline = await openRecord("inline");
  const sheet = nearestOverflowRule(inline);
  // The sheet scrolls on the block axis and fills the list's root, which fills its room.
  expect(overflowClasses(sheet).map(({ name }) => name)).not.toHaveLength(0);
  for (const { name } of overflowClasses(sheet)) expect(["overflow-auto", "overflow-y-auto"]).toContain(name);
  expect(inlineOverflow(sheet)).toBe("");
  expect([...sheet.classList]).toEqual(expect.arrayContaining(["h-full", "min-h-0"]));
  expect([...sheet.parentElement!.classList]).toEqual(expect.arrayContaining(["h-full", "min-h-0"]));
  cleanup();

  const split = await openRecord("split");
  const pane = nearestOverflowRule(split);
  // The nearest rule is the content pane's element, the one the panel library styles inline.
  const panel = pane.parentElement!;
  expect(panel.hasAttribute("data-panel"), `the record's nearest overflow rule is a pane, not "${pane.className}"`).toBe(true);
  expect(panel.id).toBe("content");
  expect(pane.style.overflow).toBe("auto");
  // Each axis too: after one axis is set inline this environment still reads `overflow` as "auto",
  // where a browser clips that axis.
  expect(["", "auto"]).toContain(pane.style.overflowX);
  expect(["", "auto"]).toContain(pane.style.overflowY);
  // A class on the pane is inert against that inline style unless it is `!important`, or aimed at
  // the pane's children, where it would style the record's own root.
  expect(overflowClasses(pane).filter(({ important, children }) => important || children)).toEqual([]);
  // The list's own workbench fills the list's root (the library's inline height), and the root
  // fills its room, as the inline sheet does.
  const group = panel.parentElement!;
  expect(group.hasAttribute("data-group")).toBe(true);
  expect(group.style.height).toBe("100%");
  expect([...group.parentElement!.classList]).toEqual(expect.arrayContaining(["h-full", "min-h-0"]));
});
