// @vitest-environment happy-dom

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { NuqsTestingAdapter } from "nuqs/adapters/testing";
import { describe, expect, test, vi } from "vitest";

import { TopMenuTabs } from "./TopMenuTabs";

describe("TopMenuTabs", () => {
  test("uses Base UI tab keyboard navigation and writes the query owner", async () => {
    const onUrlUpdate = vi.fn();
    render(
      <NuqsTestingAdapter
        searchParams="?tab=all"
        hasMemory
        onUrlUpdate={onUrlUpdate}
      >
        <TopMenuTabs
          tabs={[
            { id: "all", label: "All" },
            { id: "starred", label: "Starred" },
          ]}
        />
      </NuqsTestingAdapter>,
    );

    const all = screen.getByRole("tab", { name: "All" });
    all.focus();
    fireEvent.keyDown(all, { key: "ArrowRight" });

    const starred = screen.getByRole("tab", { name: "Starred" });
    expect(starred.getAttribute("tabindex")).toBe("0");
    fireEvent.click(starred);
    await waitFor(() => {
      expect(starred.getAttribute("aria-selected")).toBe("true");
    });
    expect(onUrlUpdate.mock.calls.at(-1)?.[0].searchParams.get("tab"))
      .toBe("starred");
  });
});
