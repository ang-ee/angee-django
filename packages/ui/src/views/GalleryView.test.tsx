// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";

import { GalleryView } from "./GalleryView";

afterEach(() => cleanup());

interface Cover extends Record<string, unknown> {
  id: string;
  title: string;
  subtitle: string;
}

const ROWS: Cover[] = [
  { id: "a", title: "Onboarding map", subtitle: "PNG" },
  { id: "b", title: "Quarterly brief", subtitle: "PDF" },
];

describe("GalleryView", () => {
  test("renders one card per row with title + subtitle and fires click", () => {
    const onCardClick = vi.fn();
    render(
      <GalleryView<Cover>
        rows={ROWS}
        subtitleField="subtitle"
        onCardClick={onCardClick}
      />,
    );
    expect(screen.getByText("Onboarding map")).toBeTruthy();
    expect(screen.getByText("PDF")).toBeTruthy();

    fireEvent.click(screen.getByText("Onboarding map"));
    expect(onCardClick).toHaveBeenCalledWith(
      expect.objectContaining({ id: "a" }),
    );
  });

  test("falls back to the title initial when no image field is given", () => {
    render(<GalleryView<Cover> rows={ROWS} />);
    expect(screen.getByText("O")).toBeTruthy();
    expect(screen.getByText("Q")).toBeTruthy();
  });

  test("uses the shared interactive frame with actions and omits empty footers", () => {
    const onCardClick = vi.fn();
    const view = render(
      <GalleryView<Cover>
        rows={[ROWS[0]!]}
        onCardClick={onCardClick}
        cardActions={() => <button type="button">Archive</button>}
      />,
    );

    fireEvent.click(screen.getByText("Onboarding map"));
    expect(onCardClick).toHaveBeenCalledWith(ROWS[0]);
    expect(view.container.querySelector("footer")).toBeTruthy();

    view.rerender(
      <GalleryView<Cover>
        rows={[ROWS[0]!]}
        onCardClick={onCardClick}
        cardActions={() => null}
      />,
    );
    expect(view.container.querySelector("footer")).toBeNull();
  });
});
