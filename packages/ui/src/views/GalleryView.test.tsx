// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";

import { GalleryView } from "./GalleryView";
import { InAppLinkProvider } from "../lib/in-app-link";

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
  test("selection is outside the card link and never opens the record", () => {
    const navigate = vi.fn();
    const onCardClick = vi.fn();
    const onToggleSelected = vi.fn();
    render(<InAppLinkProvider navigate={navigate}><GalleryView rows={[ROWS[0]!]}
      cardHref={() => "/records/a"} onCardClick={onCardClick} onToggleSelected={onToggleSelected}
    /></InAppLinkProvider>);
    const checkbox = screen.getByRole("checkbox", { name: "Select Onboarding map" });
    expect(checkbox.closest("a")).toBeNull();
    fireEvent.click(checkbox);
    expect(onToggleSelected).toHaveBeenCalledExactlyOnceWith("a", true);
    expect(onCardClick).not.toHaveBeenCalled();
    expect(navigate).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("link"));
    expect(onCardClick).toHaveBeenCalledExactlyOnceWith(ROWS[0]);
    expect(navigate).toHaveBeenCalledExactlyOnceWith("/records/a");
  });
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
