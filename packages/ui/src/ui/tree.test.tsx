// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";

import { toneGlyph } from "../lib/tones";
import { Tree, treeVariants, type TreeNode } from "./tree";
import { InlineTextAction } from "./inline-text-action";

afterEach(cleanup);

const STARRED: readonly TreeNode[] = [
  { id: "a", label: "Starred", icon: "star", iconTone: "warning" },
  { id: "b", label: "Plain", icon: "folder" },
];

describe("Tree iconTone", () => {
  test("tints the icon wrapper with the glyph-tone override class", () => {
    const { container } = render(<Tree nodes={STARRED} />);
    const tinted = container.querySelector('[class*="text-warning-text"]');
    expect(tinted).not.toBeNull();
    // The override is the glyph-scoped !important class so it beats the row's
    // own `[&_.glyph]:text-fg-subtle`.
    expect(tinted?.className).toBe(toneGlyph("warning"));
  });

  test("leaves an untoned icon to the row's default glyph color", () => {
    const { getAllByText } = render(<Tree nodes={STARRED} />);
    // The plain row's icon wrapper carries no tone class.
    const plain = getAllByText("Plain")[0]?.closest('[role="treeitem"]');
    expect(plain?.querySelector('[class*="text-warning-text"]')).toBeNull();
  });
});

describe("treeVariants", () => {
  test("composes the active and drop-target highlight states", () => {
    const active = treeVariants({ active: true });
    expect(active).toContain("selected");
    expect(active).toContain("font-medium");
    const drop = treeVariants({ dropTarget: true });
    expect(drop).toContain("ring-brand");
    // Base-only is the resting row.
    expect(treeVariants({})).toContain("text-fg-2");
  });
});

test("inline rename keeps editing keys while focused treeitems retain navigation and activation", () => {
  const select = vi.fn();
  const submit = vi.fn();
  render(<Tree nodes={[
    { id: "folder", label: <InlineTextAction open value="Folder" inputLabel="Folder name" onSubmit={submit} />,
      children: [{ id: "child", label: "Child" }] },
    { id: "other", label: "Other" },
  ]} onSelect={select} />);
  const input = screen.getByRole("textbox", { name: "Folder name" });
  expect(document.activeElement).toBe(input);
  for (const key of [" ", "Enter", "ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"]) {
    expect(fireEvent.keyDown(input, { key })).toBe(true);
    expect(document.activeElement).toBe(input);
  }
  expect(select).not.toHaveBeenCalled();
  fireEvent.change(input, { target: { value: "Renamed folder" } });
  fireEvent.submit(screen.getByRole("form", { name: "Folder name" }));
  expect(submit).toHaveBeenCalledWith("Renamed folder");

  const child = screen.getByText("Child").closest('[role="treeitem"]')!;
  fireEvent.focus(child);
  fireEvent.keyDown(child, { key: "ArrowDown" });
  const other = screen.getByText("Other").closest('[role="treeitem"]')!;
  expect(document.activeElement).toBe(other);
  fireEvent.keyDown(other, { key: "Enter" });
  expect(select).toHaveBeenLastCalledWith("other");
});


test("reveal preserves manual folding across fresh arrays and cosmetic parent renders", () => {
  const tree = (label: string, selected = "child") => <Tree nodes={[
    { id: "folder", label, children: [
      { id: "child", label: "Child" }, { id: "sibling", label: "Sibling" },
    ] },
  ]} selectedId={selected} revealIds={[selected]} />;
  const view = render(tree("Folder"));
  fireEvent.click(screen.getByRole("button", { name: "Collapse" }));
  expect(screen.queryByText("Child")).toBeNull();
  view.rerender(tree("Renamed folder"));
  expect(screen.queryByText("Child")).toBeNull();
  expect(screen.getByRole("button", { name: "Expand" })).toBeTruthy();
  view.rerender(tree("Renamed folder", "sibling"));
  expect(screen.getByText("Sibling")).toBeTruthy();
});

test("newly loaded ancestors reveal a pending target without resetting other branches", () => {
  const branch = { id: "other", label: "Other", children: [{ id: "unrelated", label: "Unrelated" }] };
  const view = render(<Tree nodes={[
    branch, { id: "loaded", label: "Loaded", hasChildren: true, defaultCollapsed: true },
  ]} revealIds={["target"]} />);
  fireEvent.click(screen.getByRole("button", { name: "Collapse" }));
  view.rerender(<Tree nodes={[
    branch,
    { id: "loaded", label: "Loaded", defaultCollapsed: true,
      children: [{ id: "target", label: "Target" }] },
  ]} revealIds={["target"]} />);
  expect(screen.getByText("Target")).toBeTruthy();
  expect(screen.queryByText("Unrelated")).toBeNull();
});


test("pointer disclosure activation returns focus to the row for arrow navigation", () => {
  render(<Tree nodes={[
    { id: "folder", label: "Folder", children: [{ id: "child", label: "Child" }] },
    { id: "other", label: "Other" },
  ]} />);
  const disclosure = screen.getByRole("button", { name: "Collapse" });
  disclosure.focus();
  fireEvent.click(disclosure);
  const folder = screen.getByText("Folder").closest('[role="treeitem"]')!;
  expect(document.activeElement).toBe(folder);
  fireEvent.keyDown(document.activeElement!, { key: "ArrowDown" });
  expect(document.activeElement).toBe(screen.getByText("Other").closest('[role="treeitem"]'));
});
