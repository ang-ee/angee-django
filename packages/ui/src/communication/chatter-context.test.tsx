// @vitest-environment happy-dom

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, test } from "vitest";
import { useEffect, useRef } from "react";

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

  test("does not republish when the same owner sends the same content", () => {
    const content = {
      tabs: [{ id: "details", label: "Details", children: "Panel" }],
    } satisfies ChatterContent;
    const renders: number[] = [];

    render(
      <ChatterProvider>
        <ManualPublisher content={content} />
        <Host renders={renders} />
      </ChatterProvider>,
    );

    expect(screen.getByTestId("content").textContent).toBe("details");
    expect(renders).toEqual([1, 2]);
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

function Host({ renders }: { renders?: number[] }) {
  return <HostContent renders={renders} />;
}

function ManualPublisher({
  content,
}: {
  content: ChatterContent | null;
}): null {
  const ownerRef = useRef(Symbol("test-chatter-content"));
  const { setContent } = useChatter();

  useEffect(() => {
    setContent(ownerRef.current, content);
    setContent(ownerRef.current, content);
  }, [content, setContent]);

  return null;
}

function HostContent({ renders }: { renders?: number[] }) {
  const { content } = useChatter();
  renders?.push(renders.length + 1);
  return (
    <div data-testid="content">
      {content?.tabs?.map((tab) => tab.id).join(",") ?? "none"}
    </div>
  );
}
