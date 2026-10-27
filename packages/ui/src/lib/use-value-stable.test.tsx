// @vitest-environment happy-dom

import { act, cleanup, fireEvent, render, renderHook, screen } from "@testing-library/react";
import { startTransition, Suspense, useEffect, useState } from "react";
import { afterEach, expect, test, vi } from "vitest";

import { useValueStable } from "./use-value-stable";

afterEach(cleanup);

test("retains equal nested JSON values across object key order and replaces changed array order", () => {
  const first = { filter: { title: "alpha", states: ["open", "done"] } };
  const { result, rerender } = renderHook(({ value }) => useValueStable(value), {
    initialProps: { value: first },
  });
  rerender({ value: { filter: { states: ["open", "done"], title: "alpha" } } });
  expect(result.current).toBe(first);
  const changed = { filter: { title: "alpha", states: ["done", "open"] } };
  rerender({ value: changed });
  expect(result.current).toBe(changed);
  rerender({ value: { filter: { states: ["done", "open"], title: "alpha" } } });
  expect(result.current).toBe(changed);
});

test("keeps undefined distinct from null and JSON strings", () => {
  const { result, rerender } = renderHook<unknown, { value: unknown }>(({ value }) => useValueStable(value), {
    initialProps: { value: undefined },
  });
  expect(result.current).toBeUndefined();
  rerender({ value: null });
  expect(result.current).toBeNull();
  rerender({ value: "\0undefined" });
  expect(result.current).toBe("\0undefined");
  rerender({ value: undefined });
  expect(result.current).toBeUndefined();
});

test("an abandoned Suspense transition preserves the committed value's identity", async () => {
  type Revision = "initial" | "pending" | "retried";
  type Value = { selected: string };
  const suspended = new Promise<never>(() => {});
  const pendingRender = vi.fn();
  const committed = vi.fn<(value: Value) => void>();

  function Commit({ value, revision }: { value: Value; revision: Revision }) {
    useEffect(() => { committed(value); }, [value, revision]);
    return <output>{revision}</output>;
  }

  function Probe({ revision }: { revision: Revision }) {
    const value = useValueStable({ selected: revision === "pending" ? "other" : "current" });
    if (revision === "pending") {
      pendingRender();
      throw suspended;
    }
    return <Commit value={value} revision={revision} />;
  }

  function Host() {
    const [revision, setRevision] = useState<Revision>("initial");
    return <>
      <button onClick={() => startTransition(() => setRevision("pending"))}>Start transition</button>
      <button onClick={() => setRevision("retried")}>Keep current</button>
      <Suspense fallback={<p>Loading</p>}><Probe revision={revision} /></Suspense>
    </>;
  }

  render(<Host />);
  const initial = committed.mock.lastCall![0];
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Start transition" })); });
  expect(pendingRender).toHaveBeenCalled();
  expect(screen.getByRole("status").textContent).toBe("initial");
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Keep current" })); });
  expect(screen.getByRole("status").textContent).toBe("retried");
  expect(committed.mock.lastCall![0]).toBe(initial);
});
