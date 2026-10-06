// @vitest-environment happy-dom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";

import { DrawerProvider, useDrawerState } from "../layouts/drawer-context";
import { AppRuntimeProvider, containersFromChildren, type ComposedContainers, type ContainerScope } from "../runtime";
import { DrawerRail } from "./DrawerRail";
import { SHELL_CONTAINERS } from "./shell-containers";

afterEach(() => { cleanup(); globalThis.localStorage?.clear(); });

// The operator's logs drawer, which a product hides on its board route.
const containers: ComposedContainers = {
  ...containersFromChildren(SHELL_CONTAINERS, {
    "shell#drawers-bottom": {
      "operator.logs": { content: { title: "Logs", render: () => null }, sequence: 20 },
      "operator.tail": { content: { title: "Tail", render: () => null }, sequence: 10 },
    },
  }),
  rules: { "shell#drawers-bottom": [{ layer: "product", rank: 1, when: { route: "product.board" }, except: ["operator.logs"], exempt: [] }] },
};
const at = (route: string): ContainerScope => ({ apps: [], routes: [route] });

function StateProbe() {
  const { openId } = useDrawerState();
  return <span data-testid="open-drawer">{openId("bottom") ?? "closed"}</span>;
}

function Harness({ scope }: { scope: ContainerScope }) {
  return <AppRuntimeProvider runtime={{ containers, containerScope: scope }}>
    <DrawerProvider>
      <DrawerRail edge="bottom" />
      <DrawerRail edge="right" />
      <StateProbe />
    </DrawerProvider>
  </AppRuntimeProvider>;
}

test("the edge's tabs are the shell#drawers-<edge> children in composed order", () => {
  render(<Harness scope={at("product.list")} />);
  const rails = screen.getAllByRole("toolbar");
  expect(rails).toHaveLength(1);
  expect(rails[0]!.textContent).toBe("TailLogs");
});

test("a drawer closes when the active route no longer admits its tab", async () => {
  const { rerender } = render(<Harness scope={at("product.list")} />);
  fireEvent.click(screen.getByRole("button", { name: "Logs" }));
  expect(screen.getByTestId("open-drawer").textContent).toBe("operator.logs");
  rerender(<Harness scope={at("product.board")} />);
  await waitFor(() => expect(screen.getByTestId("open-drawer").textContent).toBe("closed"));
  expect(screen.queryByRole("button", { name: "Logs" })).toBeNull();
  expect(screen.getByRole("button", { name: "Tail" })).toBeTruthy();
});
