// @vitest-environment happy-dom

import { cleanup, render } from "@testing-library/react";
import * as React from "react";
import { afterEach, expect, test, vi } from "vitest";

import { ModalsHost, ToastProvider } from "../../feedback";
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

const COLLAPSE = "not-has-[>div>:not([hidden]):not(:empty)]:hidden";

// As a record with no verbs: a contribution that offers nothing, a clean form, an empty toolbar host.
const nothing = <>
  <SaveDiscardActions isDirty={false} onDiscard={vi.fn()} onSave={vi.fn()} />
  <span className="contents" />
  <RecordActionBar record={record} actions={[]} menuPlacement="leading" contributedActions={
    <RecordActionBar record={record} actions={[{ id: "hidden", label: "Hidden", run: vi.fn(), visibleWhen: () => false }]} />
  } />
</>;

test("a band none of whose items renders takes no row", () => {
  const band = renderBand(nothing);
  expect(bandItems(band)).toEqual([]);
  expect(band.className).toContain(COLLAPSE);
});

test("an editable record's band keeps its row with nothing in it, so its first edit shifts nothing", () => {
  const editable = renderBand(nothing, true);
  expect(bandItems(editable)).toEqual([]);
  expect(editable.className).not.toContain(COLLAPSE);
  cleanup();
  // A record that takes no edits and has no verbs still collapses.
  expect(renderBand(nothing, false).className).toContain(COLLAPSE);
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
