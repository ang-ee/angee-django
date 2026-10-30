// @vitest-environment happy-dom

import { renderHook } from "@testing-library/react";
import { describe, expect, test, vi } from "vitest";

import { useDaemonMutationRun, verbDescriptors, verbRowActions, type DaemonVerb } from "./daemon-verbs";

const base = { field: "serviceStart", variables: { id: "web" } };

describe("useDaemonMutationRun", () => {
  test("resolves on a returned payload and refetches", async () => {
    const refetch = vi.fn();
    const { result } = renderHook(() => useDaemonMutationRun(refetch));
    // The daemon returns a MutationResult; its `status` is descriptive, not a pass/fail flag.
    const run = vi.fn(async () => ({ serviceStart: { status: "started" } }));
    await expect(result.current({ ...base, run })).resolves.toBeUndefined();
    expect(refetch).toHaveBeenCalledTimes(1);
  });

  test("treats a missing root payload as failure, not silent success", async () => {
    const refetch = vi.fn();
    const { result } = renderHook(() => useDaemonMutationRun(refetch));
    await expect(result.current({ ...base, run: async () => ({}) })).rejects.toThrow("The action returned no result.");
    expect(refetch).toHaveBeenCalledTimes(1);
  });

  test("lets a transport error reach the owner and still refetches", async () => {
    const refetch = vi.fn();
    const { result } = renderHook(() => useDaemonMutationRun(refetch));
    await expect(result.current({ ...base, run: async () => { throw new Error("network down"); } })).rejects.toThrow("network down");
    expect(refetch).toHaveBeenCalledTimes(1);
  });
});

describe("verb adapters", () => {
  const perform = vi.fn(async () => undefined);
  const verbs: readonly DaemonVerb<{ name: string; active: boolean }>[] = [
    { id: "start", label: "Start", primary: true, visible: (s) => !s.active, perform },
    { id: "destroy", label: "Destroy", danger: true, placement: "menu",
      confirm: { title: "Destroy?", body: (s) => `Destroy ${s.name}?` }, perform },
  ];

  test("describe the verbs for the record action bar once", async () => {
    const [start, destroy] = verbDescriptors(verbs);
    expect(start).toMatchObject({ id: "start", primary: true, placement: "toolbar" });
    expect(start?.visibleWhen?.({ name: "web", active: false })).toBe(true);
    expect(start?.visibleWhen?.({ name: "web", active: true })).toBe(false);
    expect(destroy).toMatchObject({ id: "destroy", danger: true, placement: "menu" });
    expect(typeof destroy?.confirm === "function" ? destroy.confirm({ name: "web" }) : null)
      .toEqual({ title: "Destroy?", body: "Destroy web?", confirm: "Destroy" });
    await start?.run?.({ record: { name: "web", active: false }, values: {}, refresh: () => {}, update: async () => null, prompt: async () => null });
    expect(perform).toHaveBeenCalledWith({ name: "web", active: false });
  });

  test("describe the same verbs as row actions, disabled together while busy", () => {
    const [start, destroy] = verbRowActions(verbs, true);
    expect(start).toMatchObject({ id: "start", variant: "secondary" });
    expect(start?.disabled({ name: "web", active: false })).toBe(true);
    expect(start?.visible({ name: "web", active: true })).toBe(false);
    expect(destroy).toMatchObject({ id: "destroy", variant: "danger" });
    expect(destroy?.confirm?.body({ name: "web", active: true })).toBe("Destroy web?");
  });
});
