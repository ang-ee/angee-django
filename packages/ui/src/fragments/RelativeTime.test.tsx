// @vitest-environment happy-dom

import { act, cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";

import { RelativeTime } from "./RelativeTime";
import { AppRuntimeProvider } from "../runtime/runtime";
import { createAngeeI18nInstance } from "../runtime/i18n";
import { formatDate, setHumanDateLocale } from "../widgets/date-format";

describe("RelativeTime", () => {
  afterEach(() => {
    cleanup();
    vi.useRealTimers();
    setHumanDateLocale("en");
  });

  test("renders the fallback for invalid dates", () => {
    const { container } = render(
      <RelativeTime value="not-a-date" fallback="Unknown" />,
    );

    expect(container.textContent).toBe("Unknown");
  });

  test("uses the composed app language for relative time", () => {
    vi.setSystemTime(new Date(2026, 8, 30, 12));
    const { container } = render(<AppRuntimeProvider runtime={{ i18n: {
      language: "fr", getFixedT: () => (key) => key,
    } }}><RelativeTime value={new Date(2026, 8, 30, 10)} /></AppRuntimeProvider>);
    expect(container.textContent).toContain("il y a 2 heures");
    expect(container.querySelector("time")?.title).toContain("sept.");
  });

  test("updates the pure formatter default when the runtime changes language", async () => {
    vi.setSystemTime(new Date(2026, 8, 30));
    const i18n = createAngeeI18nInstance({}, "fr");
    render(<AppRuntimeProvider runtime={{ i18n }}><RelativeTime value={new Date(2026, 8, 29)} /></AppRuntimeProvider>);
    expect(formatDate("2026-09-29")).toBe("29 sept.");
    await act(async () => { await i18n.changeLanguage("en"); });
    expect(formatDate("2026-09-29")).toBe("Sep 29");
  });

  test.each([
    ["an ISO string", "2026-08-21T12:30:00.000Z"],
    ["a Date", new Date("2026-08-21T12:30:00.000Z")],
    ["a timestamp", Date.parse("2026-08-21T12:30:00.000Z")],
  ])("accepts %s", (_label, value) => {
    const { container } = render(<RelativeTime value={value} addSuffix={false} />);

    expect(container.querySelector("time")?.dateTime).toBe(
      "2026-08-21T12:30:00.000Z",
    );
    expect(container.querySelector("time")?.title).toBeTruthy();
  });

  test.each(["", "not-a-date", Number.NaN, new Date(Number.NaN)])(
    "renders the fallback for invalid input %p",
    (value) => {
      const { container } = render(
        <RelativeTime value={value} fallback="Unknown" />,
      );

      expect(container.textContent).toBe("Unknown");
      expect(container.querySelector("time")).toBeNull();
    },
  );
});
