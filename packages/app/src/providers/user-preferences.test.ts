import { describe, expect, test, vi } from "vitest";

import {
  createUserPreferencesPatchQueue,
  UserPreferencesQueueClosedError,
} from "./user-preferences";

describe("user preferences patch queue", () => {
  test("serializes rapid successive writes against the latest committed value", async () => {
    const first = deferred<Record<string, unknown>>();
    const persist = vi.fn()
      .mockImplementationOnce(() => first.promise)
      .mockImplementationOnce(async (preferences: Record<string, unknown>) => preferences);
    const committed = vi.fn();
    const queue = createUserPreferencesPatchQueue({
      persist,
      committed,
    });
    queue.rebase({});

    const firstWrite = queue.patch((current) => ({ ...current, first: true }));
    const secondWrite = queue.patch((current) => ({ ...current, second: true }));
    await Promise.resolve();
    await Promise.resolve();

    expect(persist).toHaveBeenCalledTimes(1);
    first.resolve({ first: true });
    await firstWrite;
    await secondWrite;

    expect(persist).toHaveBeenNthCalledWith(2, {
      first: true,
      second: true,
    });
    expect(committed).toHaveBeenLastCalledWith({ first: true, second: true });
  });

  test("preserves rail and favorites patches when they interleave", async () => {
    const persist = vi.fn(async (preferences: Record<string, unknown>) => preferences);
    const queue = createUserPreferencesPatchQueue({
      persist,
      committed: vi.fn(),
    });
    queue.rebase({});

    await Promise.all([
      queue.patch((current) => ({
        ...current,
        "chrome.rail": { expanded: false },
      })),
      queue.patch((current) => ({
        ...current,
        "resource-view.favorites": {
          version: 1,
          models: { "notes.Note": [{ id: "favorite:open", label: "Open" }] },
        },
      })),
    ]);

    expect(persist).toHaveBeenLastCalledWith({
      "chrome.rail": { expanded: false },
      "resource-view.favorites": {
        version: 1,
        models: { "notes.Note": [{ id: "favorite:open", label: "Open" }] },
      },
    });
  });

  test("keeps a live rebase that arrives while persistence is in flight", async () => {
    const first = deferred<Record<string, unknown>>();
    const persist = vi.fn()
      .mockImplementationOnce(() => first.promise)
      .mockImplementationOnce(async (preferences: Record<string, unknown>) => preferences);
    const committed = vi.fn();
    const queue = createUserPreferencesPatchQueue({ persist, committed });
    queue.rebase({ base: true });

    const inFlight = queue.patch((current) => ({ ...current, local: "stale" }));
    await Promise.resolve();
    await Promise.resolve();
    queue.rebase({ base: true, remote: "newer" });
    first.resolve({ base: true, local: "stale" });
    await inFlight;

    expect(committed).not.toHaveBeenCalled();

    await queue.patch((current) => ({ ...current, after: true }));
    expect(persist).toHaveBeenNthCalledWith(2, {
      base: true,
      remote: "newer",
      after: true,
    });
    expect(committed).toHaveBeenCalledWith({
      base: true,
      remote: "newer",
      after: true,
    });
  });

  test("rolls a failed patch back before applying the next queued write", async () => {
    const persist = vi.fn()
      .mockRejectedValueOnce(new Error("rail write failed"))
      .mockImplementationOnce(async (preferences: Record<string, unknown>) => preferences);
    const committed = vi.fn();
    const queue = createUserPreferencesPatchQueue({
      persist,
      committed,
    });
    queue.rebase({ retained: true });

    const failed = queue.patch((current) => ({ ...current, failed: true }));
    const recovered = queue.patch((current) => ({ ...current, recovered: true }));

    await expect(failed).rejects.toThrow("rail write failed");
    await recovered;

    expect(persist).toHaveBeenNthCalledWith(2, {
      retained: true,
      recovered: true,
    });
    expect(committed).toHaveBeenCalledOnce();
    expect(committed).toHaveBeenCalledWith({ retained: true, recovered: true });
  });

  test("closes queued-behind writes before they can persist for a new session", async () => {
    const first = deferred<Record<string, unknown>>();
    const persist = vi.fn(() => first.promise);
    const committed = vi.fn();
    const queue = createUserPreferencesPatchQueue({ persist, committed });
    queue.rebase({ session: "old" });

    const inFlight = queue.patch((current) => ({ ...current, first: true }));
    const queued = queue.patch((current) => ({ ...current, second: true }));
    await Promise.resolve();
    await Promise.resolve();
    expect(persist).toHaveBeenCalledOnce();

    const queuedResult = expect(queued).rejects.toBeInstanceOf(
      UserPreferencesQueueClosedError,
    );
    queue.close();
    first.resolve({ session: "old", first: true });

    await expect(inFlight).resolves.toBeUndefined();
    await queuedResult;
    expect(persist).toHaveBeenCalledOnce();
    expect(committed).not.toHaveBeenCalled();
  });

  test("survives StrictMode's close-then-reopen effect cycle", async () => {
    const persist = vi.fn(async (next: Record<string, unknown>) => next);
    const committed = vi.fn();
    const queue = createUserPreferencesPatchQueue({ persist, committed });

    // StrictMode runs the owning effect as mount → cleanup → mount, so the
    // memoized queue sees open(), close(), open() before any user patch.
    queue.open();
    queue.close();
    queue.open();

    await queue.patch(() => ({ marker: true }));
    expect(persist).toHaveBeenCalledOnce();
    expect(committed).toHaveBeenCalledWith({ marker: true });
  });
});

function deferred<T>(): {
  promise: Promise<T>;
  resolve: (value: T) => void;
} {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((nextResolve) => {
    resolve = nextResolve;
  });
  return { promise, resolve };
}
