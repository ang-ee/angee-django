// @vitest-environment happy-dom

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, test, vi } from "vitest";

import { GalleryView } from "./GalleryView";
import { InAppLinkProvider } from "../lib/in-app-link";

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
    expect(onToggleSelected).toHaveBeenCalledExactlyOnceWith("a", true, ROWS[0]);
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

  test("frames each card on the shared recipe: flush by default, inset on request", () => {
    const view = render(<GalleryView<Cover> rows={[ROWS[0]!]} renderCard={(row) => <span>{row.title}</span>} />);
    const card = view.container.querySelector("[data-slot='gallery-card']");
    expect(card?.getAttribute("data-frame")).toBe("flush");
    expect(card?.className).toContain("p-0");
    view.rerender(<GalleryView<Cover> rows={[ROWS[0]!]} cardFrame="inset" renderCard={(row) => <span>{row.title}</span>} />);
    expect(view.container.querySelector("[data-slot='gallery-card']")?.className).toContain("p-3");
  });

  test("a selected card wears the card recipe's selected state", () => {
    const view = render(<GalleryView<Cover> rows={ROWS} selectedIds={new Set(["b"])} onToggleSelected={() => undefined} />);
    const cards = view.container.querySelectorAll("[data-slot='gallery-card']");
    expect(cards[0]?.className).not.toContain("border-brand");
    expect(cards[1]?.className).toContain("border-brand");
    expect(screen.getByRole("checkbox", { name: "Select Quarterly brief" })).toBeTruthy();
  });

  test("renderCard receives the gallery context with the card's selection", () => {
    const renderCard = vi.fn((row: Cover) => <span>{row.title}</span>);
    render(<GalleryView<Cover> rows={ROWS} renderCard={renderCard} selectedIds={new Set(["b"])} />);
    expect(renderCard).toHaveBeenCalledWith(ROWS[0], { kind: "gallery", selected: false });
    expect(renderCard).toHaveBeenCalledWith(ROWS[1], { kind: "gallery", selected: true });
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
