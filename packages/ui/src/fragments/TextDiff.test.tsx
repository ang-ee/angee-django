// @vitest-environment happy-dom

import { render, screen } from "@testing-library/react";
import { describe, expect, test } from "vitest";

import { compareText, TextDiff } from "./TextDiff";

const BEFORE = "Doors open at seven in the north hall.";
const AFTER = "Doors open at eight in the north hall.";
const comparison = compareText(BEFORE, AFTER);

/** The text a sighted reader sees: the visually hidden cues left out. */
function visibleText(element: Element): string {
  const copy = element.cloneNode(true) as Element;
  for (const hidden of copy.querySelectorAll(".sr-only")) hidden.remove();
  return copy.textContent ?? "";
}

function marked(container: HTMLElement, tag: "del" | "ins"): string[] {
  return [...container.querySelectorAll(tag)].map((element) => visibleText(element).trim());
}

describe("TextDiff", () => {
  test("inline shows the removed word struck through and the added word underlined", () => {
    const { container } = render(<TextDiff comparison={comparison} />);
    expect(marked(container, "del")).toEqual(["seven"]);
    expect(marked(container, "ins")).toEqual(["eight"]);
    expect(screen.getByRole("group", { name: "1 removal, 1 addition" })).toBeTruthy();
  });

  test("each mark carries a visually hidden removed or added cue", () => {
    const { container } = render(<TextDiff comparison={comparison} />);
    expect(container.querySelector("del .sr-only")?.textContent?.trim()).toBe("Removed:");
    expect(container.querySelector("ins .sr-only")?.textContent?.trim()).toBe("Added:");
  });

  test("the before side keeps only the earlier text and its removals", () => {
    const { container } = render(<TextDiff comparison={comparison} side="before" lang="en-GB" />);
    const group = screen.getByRole("group", { name: "1 removal" });
    expect(group.getAttribute("lang")).toBe("en-GB");
    expect(visibleText(group)).toBe(BEFORE);
    expect(marked(container, "ins")).toEqual([]);
  });

  test("the after side keeps only the later text and its additions", () => {
    const { container } = render(<TextDiff comparison={comparison} side="after" lang="es" />);
    const group = screen.getByRole("group", { name: "1 addition" });
    expect(group.getAttribute("lang")).toBe("es");
    expect(visibleText(group)).toBe(AFTER);
    expect(marked(container, "del")).toEqual([]);
  });

  test("one comparison serves both sides of a pair", () => {
    render(<><TextDiff comparison={comparison} side="before" /><TextDiff comparison={comparison} side="after" /></>);
    expect(screen.getByRole("group", { name: "1 removal" })).toBeTruthy();
    expect(screen.getByRole("group", { name: "1 addition" })).toBeTruthy();
  });

  test("counts each changed run, not each word, in the summary", () => {
    // "two" → "deux" and "four" → "quatre five": two removed runs, two added runs.
    render(<TextDiff comparison={compareText("one two three four", "one deux three quatre five")} />);
    expect(screen.getByRole("group", { name: "2 removals, 2 additions" })).toBeTruthy();
  });

  test("names identical texts as unchanged and marks nothing", () => {
    const { container } = render(<TextDiff comparison={compareText(BEFORE, BEFORE)} />);
    expect(visibleText(screen.getByRole("group", { name: "No changes" }))).toBe(BEFORE);
    expect(container.querySelectorAll("del, ins")).toHaveLength(0);
  });

  test("texts too different to compare in time show as a whole replacement with a note", () => {
    const words = (prefix: string) => Array.from({ length: 4000 }, (_, index) => `${prefix}${index}`).join(" ");
    const unrelated = compareText(words("alpha"), words("omega"));
    expect(unrelated.changes).toBeNull();
    const { container } = render(<TextDiff comparison={unrelated} />);
    const note = "Too different to compare word by word, so shown as a whole replacement.";
    expect(screen.getByRole("group", { name: note })).toBeTruthy();
    expect(screen.getByText(note)).toBeTruthy();
    expect(marked(container, "del")).toEqual([words("alpha")]);
    expect(marked(container, "ins")).toEqual([words("omega")]);
  });
});
