// @vitest-environment happy-dom

import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";
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

  test("honors an open request made before the pane controller registers", () => {
    const expand = vi.fn();
    render(<ChatterProvider defaultCollapsed>
      <PendingOpen controller={{ collapsed: true, collapse: vi.fn(), expand, toggle: vi.fn() }} />
    </ChatterProvider>);

    expect(expand).toHaveBeenCalledOnce();
  });

  test("expands a replacement pane when its native handle becomes ready", () => {
    let bridge: ReturnType<typeof useChatter> | null = null;
    const expand = vi.fn();
    render(<ChatterProvider defaultCollapsed>
      <CaptureBridge onRender={(value) => { bridge = value; }} />
    </ChatterProvider>);

    act(() => bridge!.registerSecondaryController({
      collapsed: true, ready: true, collapse: vi.fn(), expand: vi.fn(), toggle: vi.fn(),
    }));
    act(() => {
      bridge!.registerSecondaryController(null);
      bridge!.setCollapsed(false);
      bridge!.registerSecondaryController({
        collapsed: true, ready: false, collapse: vi.fn(), expand, toggle: vi.fn(),
      });
    });
    expect(expand).not.toHaveBeenCalled();

    act(() => bridge!.registerSecondaryController({
      collapsed: true, ready: true, collapse: vi.fn(), expand, toggle: vi.fn(),
    }));
    expect(expand).toHaveBeenCalledOnce();

    // A later user collapse is a new state, not the pending open request.
    act(() => bridge!.registerSecondaryController({
      collapsed: true, ready: true, collapse: vi.fn(), expand, toggle: vi.fn(),
    }));
    expect(expand).toHaveBeenCalledOnce();
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

function PendingOpen({ controller }: {
  controller: { collapsed: boolean; collapse: () => void; expand: () => void; toggle: () => void };
}): null {
  const { registerSecondaryController, setCollapsed } = useChatter();
  useEffect(() => {
    setCollapsed(false);
    registerSecondaryController(controller);
    return () => registerSecondaryController(null);
  }, [controller, registerSecondaryController, setCollapsed]);
  return null;
}

function CaptureBridge({ onRender }: {
  onRender: (value: ReturnType<typeof useChatter>) => void;
}): null {
  onRender(useChatter());
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
