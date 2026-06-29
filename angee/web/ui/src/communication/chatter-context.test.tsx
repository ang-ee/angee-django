// @vitest-environment happy-dom

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, test } from "vitest";

import {
  ChatterProvider,
  useChatter,
  useChatterContent,
  type ChatterContent,
} from "./chatter-context";

afterEach(() => cleanup());

describe("ChatterProvider", () => {
  test("treats an empty tab contribution as no published content", () => {
    render(
      <ChatterProvider>
        <Publisher content={{ tabs: [] }} />
        <Host />
      </ChatterProvider>,
    );

    expect(screen.getByTestId("content").textContent).toBe("none");
  });

  test("publishes non-empty tab contributions", () => {
    render(
      <ChatterProvider>
        <Publisher
          content={{
            tabs: [{ id: "details", label: "Details", children: "Panel" }],
          }}
        />
        <Host />
      </ChatterProvider>,
    );

    expect(screen.getByTestId("content").textContent).toBe("details");
  });
});

function Publisher({
  content,
}: {
  content: ChatterContent | null;
}): null {
  useChatterContent(content);
  return null;
}

function Host() {
  const { content } = useChatter();
  return (
    <div data-testid="content">
      {content?.tabs?.map((tab) => tab.id).join(",") ?? "none"}
    </div>
  );
}
