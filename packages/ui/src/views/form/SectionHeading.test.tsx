// @vitest-environment happy-dom

import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";

import { SectionHeading } from "./SectionHeading";

test("places the label, summary, hint, and audience in one section heading", () => {
  const { container } = render(<SectionHeading label="Messages" count="3" summary="2 replied"
    hint="Usually shared with the team" audience="Team members" />);
  const heading = screen.getByRole("heading", { name: "Messages" });
  expect(heading.className).toContain("uppercase");
  expect(container.firstElementChild?.textContent).toBe("Messages32 repliedUsually shared with the teamTeam members");
  expect(screen.getByText("Team members").className).toContain("ml-auto");
});
