// @vitest-environment happy-dom

import { render, screen } from "@testing-library/react";
import { describe, expect, test } from "vitest";

import { TextDiff } from "./TextDiff";

const BEFORE = "Doors open at seven in the north hall.";
const AFTER = "Doors open at eight in the north hall.";

function marked(container: HTMLElement, tag: "del" | "ins"): string[] {
  return [...container.querySelectorAll(tag)].map((element) => element.textContent?.trim() ?? "");
}

describe("TextDiff", () => {
  test("inline shows the removed word struck through and the added word underlined", () => {
    const { container } = render(<TextDiff before={BEFORE} after={AFTER} />);
    expect(marked(container, "del")).toEqual(["seven"]);
    expect(marked(container, "ins")).toEqual(["eight"]);
    expect(screen.getByRole("group", { name: "1 removal, 1 addition" })).toBeTruthy();
  });

  test("the before side keeps only the earlier text and its removals", () => {
    const { container } = render(<TextDiff before={BEFORE} after={AFTER} side="before" lang="en-GB" />);
    const group = screen.getByRole("group", { name: "1 removal" });
    expect(group.getAttribute("lang")).toBe("en-GB");
    expect(group.textContent).toBe(BEFORE);
    expect(marked(container, "ins")).toEqual([]);
  });

  test("the after side keeps only the later text and its additions", () => {
    const { container } = render(<TextDiff before={BEFORE} after={AFTER} side="after" lang="es" />);
    const group = screen.getByRole("group", { name: "1 addition" });
    expect(group.getAttribute("lang")).toBe("es");
    expect(group.textContent).toBe(AFTER);
    expect(marked(container, "del")).toEqual([]);
  });

  test("counts each changed run, not each word, in the summary", () => {
    // "two" → "deux" and "four" → "quatre five": two removed runs, two added runs.
    render(<TextDiff before="one two three four" after="one deux three quatre five" />);
    expect(screen.getByRole("group", { name: "2 removals, 2 additions" })).toBeTruthy();
  });

  test("names identical texts as unchanged and marks nothing", () => {
    const { container } = render(<TextDiff before={BEFORE} after={BEFORE} />);
    expect(screen.getByRole("group", { name: "No changes" }).textContent).toBe(BEFORE);
    expect(container.querySelectorAll("del, ins")).toHaveLength(0);
  });
});
