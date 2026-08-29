// @vitest-environment happy-dom

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, test } from "vitest";
import { useEffect, useRef, type ReactElement, type ReactNode } from "react";

import {
  PrimaryPaneProvider,
  usePrimaryPaneContent,
} from "./primary-pane-context";

afterEach(() => cleanup());

describe("PrimaryPaneProvider", () => {
  test("does not republish when the same owner sends the same node", () => {
    const pane = <div>Pane</div>;
    const renders: number[] = [];

    render(
      <PrimaryPaneProvider>
        <Publisher node={pane} />
        <Host renders={renders} />
      </PrimaryPaneProvider>,
    );

    expect(screen.getByTestId("pane").textContent).toBe("Pane");
    expect(renders).toEqual([1, 2]);
  });
});

function Publisher({ node }: { node: ReactNode | null }): null {
  const { setNode } = usePrimaryPaneContent();
  const ownerRef = useRef(Symbol("test-primary-pane"));

  useEffect(() => {
    setNode(ownerRef.current, node);
    setNode(ownerRef.current, node);
  }, [node, setNode]);

  return null;
}

function Host({ renders }: { renders: number[] }): ReactElement {
  const { node } = usePrimaryPaneContent();
  renders.push(renders.length + 1);
  return <div data-testid="pane">{node}</div>;
}
