// @vitest-environment happy-dom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";

import { DrawerProvider, useDrawerState } from "../layouts/drawer-context";
import { AppRuntimeProvider } from "../runtime";
import { DrawerRail } from "./DrawerRail";

afterEach(() => { cleanup(); localStorage.clear(); });

const logs = { id: "logs", edge: "bottom" as const, title: "Logs", render: () => null };

function StateProbe() {
  const { openId } = useDrawerState();
  return <span data-testid="open-drawer">{openId("bottom") ?? "closed"}</span>;
}

function Harness({ drawers }: { drawers: readonly typeof logs[] }) {
  return <AppRuntimeProvider runtime={{ drawers }}>
    <DrawerProvider>
      <DrawerRail edge="bottom" />
      <StateProbe />
    </DrawerProvider>
  </AppRuntimeProvider>;
}

test("a drawer closes when the active route no longer admits its tab", async () => {
  const { rerender } = render(<Harness drawers={[logs]} />);
  fireEvent.click(screen.getByRole("button", { name: "Logs" }));
  expect(screen.getByTestId("open-drawer").textContent).toBe("logs");
  rerender(<Harness drawers={[]} />);
  await waitFor(() => expect(screen.getByTestId("open-drawer").textContent).toBe("closed"));
  expect(screen.queryByRole("button", { name: "Logs" })).toBeNull();
});
