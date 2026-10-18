// @vitest-environment happy-dom

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, test } from "vitest";
import type { Row } from "@angee/metadata";
import { AppRuntimeProvider } from "@angee/ui";

import { attentionColumn } from "./attention";

afterEach(cleanup);

describe("attentionColumn", () => {
  test("selects and renders open decisions with attention-category tags in one cluster", () => {
    const column = attentionColumn<Row>();
    expect(column.selectionPaths).toEqual([
      "has_open_decisions", "tags.id", "tags.name", "tags.attention",
    ]);

    render(<AppRuntimeProvider runtime={{ statusTones: { urgent: "danger", "needs-decision": "warning" } }}>
      {column.render?.({
        id: "bill_1",
        has_open_decisions: true,
        tags: [
          { id: "tag_urgent", name: "Urgent", attention: true },
          { id: "tag_internal", name: "Internal", attention: false },
        ],
      })}
    </AppRuntimeProvider>);

    const decision = screen.getByText("Needs attention");
    const tag = screen.getByText("Urgent");
    expect(tag.parentElement).toBe(decision.parentElement);
    expect(screen.queryByText("Internal")).toBeNull();
  });
});
