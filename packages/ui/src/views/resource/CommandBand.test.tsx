// @vitest-environment happy-dom

import { cleanup, render } from "@testing-library/react";
import * as React from "react";
import { afterEach, expect, test, vi } from "vitest";

import { ModalsHost, ToastProvider } from "../../feedback";
import { ControlBandProvider } from "../../layouts/ControlBand";
import { createUiTestProviders } from "../../testing";
import { Button } from "../../ui/button";
import { RecordActionBar } from "../form/RecordActionBar";
import { SaveDiscardActions } from "../form/SaveDiscardActions";
import { CommandBand } from "./CommandBand";

const { Provider } = createUiTestProviders();
const record = { id: "rec_1", permissions: ["write"] };

afterEach(cleanup);

// The DOM fact the band's collapse rule (`:has(>div>:not([hidden]):not(:empty))`) reads: a
// group child that is neither hidden nor empty. happy-dom does not evaluate that selector
// faithfully, so the test reads the fact through the DOM itself.
function bandItems(band: Element): Element[] {
  return [...band.children].flatMap((group) => [...group.children]).filter((child) =>
    !child.hasAttribute("hidden") && [...child.childNodes].some((node) => node.nodeType !== Node.COMMENT_NODE));
}

function renderBand(verbs: React.ReactNode, editable?: boolean): HTMLElement {
  const { container } = render(<Provider><ModalsHost><ToastProvider>
    <CommandBand verbs={verbs} editable={editable} />
  </ToastProvider></ModalsHost></Provider>);
  return container.querySelector<HTMLElement>("[role=toolbar]")!;
}

// As a record page renders it: the band claims the layout's control row.
function LayoutRow({ children }: { children: React.ReactNode }) {
  const [host, setHost] = React.useState<HTMLElement | null>(null);
  return <ControlBandProvider host={host}>
    <div data-testid="control-row" ref={setHost} />
    {children}
  </ControlBandProvider>;
}

function renderLayoutBand(verbs: React.ReactNode, editable?: boolean): HTMLElement {
  const { getByTestId } = render(<Provider><ModalsHost><ToastProvider><LayoutRow>
    <CommandBand verbs={verbs} editable={editable} />
  </LayoutRow></ToastProvider></ModalsHost></Provider>);
  return getByTestId("control-row").querySelector<HTMLElement>("[role=toolbar]")!;
}

const COLLAPSE = "not-has-[>div>:not([hidden]):not(:empty)]:hidden";
// The editable band's own pair: inline it collapses as any band; in the layout's row it keeps the row
// blank and out of the accessibility tree (`visibility: hidden`) rather than drawing an empty toolbar.
const INLINE_COLLAPSE = "not-data-layout-band:not-has-[>div>:not([hidden]):not(:empty)]:hidden";
const LAYOUT_ROW_KEPT = "data-layout-band:not-has-[>div>:not([hidden]):not(:empty)]:invisible";

// As a record with no verbs: a contribution that offers nothing, a clean form, an empty toolbar host.
const nothing = <>
  <SaveDiscardActions isDirty={false} onDiscard={vi.fn()} onSave={vi.fn()} />
  <span className="contents" />
  <RecordActionBar record={record} actions={[]} menuTrigger="icon" contributedActions={
    <RecordActionBar record={record} actions={[{ id: "hidden", label: "Hidden", run: vi.fn(), visibleWhen: () => false }]} />
  } />
</>;

test("the overflow trigger orders itself last in the verbs group, even when a button is written after the action bar", () => {
  const band = renderBand(<>
    <RecordActionBar record={record} menuTrigger="icon" actions={[
      { id: "sync", label: "Sync now", placement: "toolbar", run: vi.fn() },
      { id: "copy", label: "Copy link", placement: "menu", run: vi.fn() },
    ]} />
    <Button type="button">Pause</Button>
  </>);
  const trigger = band.querySelector<HTMLElement>("[data-overflow-trigger]");
  expect(trigger?.getAttribute("aria-label")).toBe("Actions");
  // The trigger's own rule, not the caller's order, places it: here the caller wrote a button after the bar.
  expect(trigger?.classList.contains("order-last")).toBe(true);
  const group = trigger?.parentElement;
  expect([...(group?.querySelectorAll("button") ?? [])].map((button) => button.textContent || button.getAttribute("aria-label")))
    .toEqual(["Sync now", "Actions", "Pause"]);
});

test("a band none of whose items renders takes no row", () => {
  const band = renderBand(nothing);
  expect(bandItems(band)).toEqual([]);
  expect(band.className).toContain(COLLAPSE);
});

test("an editable record's band keeps the layout's row with nothing in it, unpainted, so its first edit shifts nothing", () => {
  const editable = renderLayoutBand(nothing, true);
  expect(editable.hasAttribute("data-layout-band")).toBe(true);
  expect(bandItems(editable)).toEqual([]);
  expect(editable.classList).toContain(LAYOUT_ROW_KEPT);
  expect(editable.classList).not.toContain(COLLAPSE);
  cleanup();
  // A record that takes no edits and has no verbs still collapses.
  const readOnly = renderLayoutBand(nothing, false);
  expect(readOnly.classList).toContain(COLLAPSE);
  expect(readOnly.classList).not.toContain(LAYOUT_ROW_KEPT);
});

test("an editable record's band inline, in a card or a dialog, collapses with nothing in it", () => {
  const inline = renderBand(nothing, true);
  expect(inline.hasAttribute("data-layout-band")).toBe(false);
  expect(bandItems(inline)).toEqual([]);
  expect(inline.classList).toContain(INLINE_COLLAPSE);
});

test("the band returns with its first item: an edit's Save and Discard, a verb", () => {
  const dirty = renderBand(<>
    {nothing}
    <SaveDiscardActions isDirty onDiscard={vi.fn()} onSave={vi.fn()} />
  </>);
  expect(bandItems(dirty)).toHaveLength(1);
  cleanup();
  const verb = renderBand(<>{nothing}<Button type="button">Approve</Button></>);
  expect(bandItems(verb).map((item) => item.textContent)).toEqual(["Approve"]);
});
