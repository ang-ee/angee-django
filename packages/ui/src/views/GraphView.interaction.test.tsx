// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, test, vi } from "vitest";

import { GraphView } from "./GraphView";

beforeAll(() => {
  class ResizeObserverStub {
    observe(): void {}
    unobserve(): void {}
    disconnect(): void {}
  }
  Object.defineProperty(globalThis, "ResizeObserver", {
    configurable: true,
    value: ResizeObserverStub,
  });
});

afterEach(() => {
  cleanup();
});

describe("GraphView interactions", () => {
  test("selects a node through the real xyflow canvas", async () => {
    const onNodeSelect = vi.fn();

    render(
      <GraphView
        className="h-[360px] w-[520px]"
        nodes={[
          { id: "draft", kind: "handler", title: "Draft", code: "handler" },
          { id: "review", kind: "gate", title: "Review", code: "gate" },
        ]}
        edges={[
          {
            id: "draft-review",
            source: "draft",
            target: "review",
            kind: "default",
          },
        ]}
        nodeStyles={{
          handler: {
            width: 160,
            height: 72,
            borderColor: "var(--border-subtle)",
          },
          gate: {
            width: 160,
            height: 72,
            borderColor: "var(--border-subtle)",
          },
        }}
        onNodeSelect={onNodeSelect}
      />,
    );

    fireEvent.click(screen.getByText("Draft"));

    await waitFor(() => {
      expect(onNodeSelect).toHaveBeenCalledWith(
        expect.objectContaining({ id: "draft" }),
      );
    });
  });
});
