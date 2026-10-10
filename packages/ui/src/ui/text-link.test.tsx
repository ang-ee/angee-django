// @vitest-environment happy-dom

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";

import { NavLink, navLinkVariants } from "./nav-link";
import { TextLink, textLinkVariants } from "./text-link";

afterEach(cleanup);

test("the deprecated default variant translates to NavLink inline", () => {
  render(
    <>
      <TextLink href="/compat">Compat</TextLink>
      <NavLink href="/canonical" variant="inline">Canonical</NavLink>
    </>,
  );

  expect(screen.getByRole("link", { name: "Compat" }).className)
    .toBe(screen.getByRole("link", { name: "Canonical" }).className);
  expect(textLinkVariants({ variant: "default" }))
    .toBe(navLinkVariants({ variant: "inline" }));
});
