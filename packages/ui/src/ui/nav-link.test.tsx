// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { NavLink } from "./nav-link";

afterEach(cleanup);

test("accepts an optional href when a slotted anchor owns the destination", () => {
  render(<NavLink asChild><a href="/records/7">Slotted</a></NavLink>);

  expect(screen.getByRole("link", { name: "Slotted" }).getAttribute("href")).toBe("/records/7");
});

test("defaults new-tab rel and exposes active, disabled and external render state", () => {
  render(
    <NavLink
      active
      href="/records/7"
      target="_blank"
      render={(props, state) => (
        <a
          {...props}
          data-active={state.active}
          data-disabled={state.disabled}
          data-external={state.external}
        />
      )}
    >
      Record
    </NavLink>,
  );

  const link = screen.getByRole("link", { name: "Record" });
  expect(link.getAttribute("rel")).toBe("noopener noreferrer");
  expect(link.dataset).toMatchObject({ active: "true", disabled: "false", external: "true" });
});

test("muted and block-card variants retain their owned visual recipes", () => {
  render(
    <>
      <NavLink href="/muted" variant="muted">Muted</NavLink>
      <NavLink href="/card" variant="block-card">Card</NavLink>
    </>,
  );

  expect(screen.getByRole("link", { name: "Muted" }).className).toContain("text-fg-muted");
  const card = screen.getByRole("link", { name: "Card" });
  expect(card.className).toContain("rounded-6");
  expect(card.className).toContain("border-border-subtle");
  expect(card.className).toContain("hover:border-border-strong");
  expect(card.className).toContain("hover:shadow-sm");
});

test("directional affordances append a decorative registry glyph", () => {
  render(<NavLink href="/next" variant="inline" affordance="forward">Next</NavLink>);

  const link = screen.getByRole("link", { name: "Next" });
  expect(link.className).toContain("inline-flex");
  expect(link.className).toContain("gap-1");
  expect(link.lastElementChild?.classList.contains("glyph")).toBe(true);
  expect(link.lastElementChild?.getAttribute("aria-hidden")).toBe("true");
});

test("disabled links are unfocusable and do not activate", () => {
  const onClick = vi.fn();
  render(<NavLink disabled href="/records/7" onClick={onClick}>Disabled</NavLink>);

  const link = screen.getByRole("link", { name: "Disabled" });
  expect(link.getAttribute("aria-disabled")).toBe("true");
  expect(link.getAttribute("tabindex")).toBe("-1");
  expect(fireEvent.click(link)).toBe(false);
  expect(onClick).not.toHaveBeenCalled();
});
