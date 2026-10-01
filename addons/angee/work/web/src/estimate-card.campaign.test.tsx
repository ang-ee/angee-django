// @vitest-environment happy-dom

import { createAngeeI18nInstance } from "@angee/ui/runtime/i18n";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { enWorkMessages } from "./i18n";
import { WorkTaskCard } from "./task-work";

vi.mock("@angee/ui", () => ({
  Group: () => null,
  Field: () => null,
  RelativeTime: () => null,
  createNamespaceT: () => () => translator.t.bind(translator),
}));

const translator = createAngeeI18nInstance({ work: enWorkMessages });
afterEach(cleanup);

test.each([
  ["HOURS", 1, "1 hour"], ["HOURS", 0.5, "0.5 hours"], ["HOURS", 0, "0 hours"],
  ["DAYS", 1, "1 day"], ["DAYS", 1.25, "1.25 days"], ["WEEKS", 1, "1 week"],
  ["WEEKS", 2.5, "2.5 weeks"], ["LINEAR", 1, "1 point"], ["FIBONACCI", 2, "2 points"],
  ["EXPONENTIAL", 4, "4 points"], ["TSHIRT", 3, "M"],
] as const)("board card renders %s estimate %s as %s", (scale, value, expected) => {
  render(<WorkTaskCard task={{ id: "tsk_card", title: "Work item", work_key: "WORK-1", estimate: value }} estimateScale={scale} />);
  expect(screen.getByText(expected)).toBeTruthy();
  expect(screen.getByText("WORK-1")).toBeTruthy();
});

test("board card omits the estimate when the queue disables estimates", () => {
  const { container } = render(<WorkTaskCard task={{ id: "tsk_card", title: "Work item", work_key: "WORK-1", estimate: 3 }} estimateScale="NONE" />);
  expect(container.textContent).toBe("WORK-1Work item");
});
