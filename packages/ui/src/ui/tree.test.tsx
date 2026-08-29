// @vitest-environment happy-dom

import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, test } from "vitest";

import { toneGlyph } from "../lib/tones";
import { Tree, treeVariants, type TreeNode } from "./tree";

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
    expect(active).toContain("bg-brand-soft");
    expect(active).toContain("font-medium");
    const drop = treeVariants({ dropTarget: true });
    expect(drop).toContain("ring-brand");
    // Base-only is the resting row.
    expect(treeVariants({})).toContain("text-fg-2");
  });
});
