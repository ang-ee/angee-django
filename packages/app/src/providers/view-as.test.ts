import { describe, expect, test, vi } from "vitest";
import { createViewAsProvider } from "./view-as";

describe("view-as state", () => {
  test("changes the transport target synchronously, settles pending, and exits on a new app", async () => {
    let settle!: () => void;
    const changed = vi.fn(() => new Promise<void>((resolve) => { settle = resolve; }));
    const provider = createViewAsProvider({ changed });
    const listener = vi.fn();
    const unsubscribe = provider.subscribe(listener);
    const entered = provider.enter("person-1");
    expect(provider.getUserId()).toBe("person-1");
    expect(provider.getSnapshot()).toMatchObject({ pending: true, error: null });
    await provider.enter("person-2");
    expect(changed).toHaveBeenCalledTimes(1);
    settle(); await entered;
    expect(provider.getSnapshot().pending).toBe(false);
    const exited = provider.exit();
    expect(provider.getUserId()).toBeNull();
    settle(); await exited;
    expect(changed.mock.calls).toEqual([["person-1"], [null]]);
    expect(listener).toHaveBeenCalledTimes(4);
    unsubscribe();
    expect(createViewAsProvider({ changed }).getSnapshot()).toEqual({ viewAs: null, pending: false, error: null });
  });

  test("a denied or revoked preview restores the real actor and exposes a bounded error", async () => {
    const changed = vi.fn().mockRejectedValueOnce(new Error("private transport details")).mockResolvedValue(undefined);
    const provider = createViewAsProvider({ changed });
    await provider.enter("denied-person");
    expect(changed.mock.calls).toEqual([["denied-person"], [null]]);
    expect(provider.getSnapshot()).toEqual({ viewAs: null, pending: false, error: "Could not preview this person." });
  });

  test("auth reset invalidates a late failed transition", async () => {
    let reject!: (error: Error) => void;
    const changed = vi.fn(() => new Promise<void>((_resolve, no) => { reject = no; }));
    const provider = createViewAsProvider({ changed });
    const entered = provider.enter("person-1");
    provider.reset();
    reject(new Error("old session")); await entered;
    expect(changed).toHaveBeenCalledTimes(1);
    expect(provider.getSnapshot()).toEqual({ viewAs: null, pending: false, error: null });
  });
});
