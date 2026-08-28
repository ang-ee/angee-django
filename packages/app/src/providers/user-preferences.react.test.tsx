// @vitest-environment happy-dom

import * as React from "react";
import {
  act,
  cleanup,
  render,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import {
  ModelMetadataProvider,
  schemaFieldMetadataFromDataResources,
} from "@angee/metadata";
import { testDataResource } from "@angee/metadata/testing";
import {
  AppRuntimeProvider,
  type RuntimeUserPreferences,
  type RuntimeUserPreferencesPatch,
} from "@angee/ui/runtime";
import {
  ResourceViewProvider,
  useResourceView,
  type ResourceViewContextValue,
} from "@angee/ui/views/resource-view-context";

import { createUserPreferencesPatchQueue } from "./user-preferences";

const METADATA = schemaFieldMetadataFromDataResources([
  testDataResource("notes.Note"),
]);

describe("resource favorites through the real preference queue", () => {
  beforeEach(() => installLocalStorageStub());
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  test("serializes two deferred same-model saves and clears optimism in queue order", async () => {
    const pending: Array<Deferred<RuntimeUserPreferences>> = [];
    const submitted: RuntimeUserPreferences[] = [];
    const persist = vi.fn((next: RuntimeUserPreferences) => {
      submitted.push(next);
      const write = deferred<RuntimeUserPreferences>();
      pending.push(write);
      return write.promise;
    });
    const captured = captureRef();
    renderQueueFavorites({ available: true, captured, persist });

    act(() => captured.current?.saveFavorite?.("First"));
    await waitFor(() => {
      expect(captured.current?.savedFavorites.map(({ label }) => label))
        .toEqual(["First"]);
      expect(persist).toHaveBeenCalledOnce();
    });
    act(() => captured.current?.saveFavorite?.("Second"));
    expect(captured.current?.savedFavorites.map(({ label }) => label)).toEqual([
      "First",
      "Second",
    ]);
    expect(persist).toHaveBeenCalledOnce();

    await act(async () => pending[0]?.resolve(submitted[0] ?? {}));
    await waitFor(() => expect(persist).toHaveBeenCalledTimes(2));
    expect(captured.current?.savedFavorites.map(({ label }) => label)).toEqual([
      "First",
      "Second",
    ]);

    await act(async () => pending[1]?.resolve(submitted[1] ?? {}));
    await waitFor(() => {
      expect(captured.current?.savedFavorites.map(({ label }) => label))
        .toEqual(["First", "Second"]);
    });
  });

  test("drops in-flight optimism when availability changes", async () => {
    const write = deferred<RuntimeUserPreferences>();
    const persist = vi.fn((_next: RuntimeUserPreferences) => write.promise);
    const captured = captureRef();
    const view = renderQueueFavorites({ available: true, captured, persist });

    act(() => captured.current?.saveFavorite?.("Pending"));
    await waitFor(() => expect(persist).toHaveBeenCalledOnce());
    expect(captured.current?.savedFavorites).toHaveLength(1);

    view.rerender(
      <QueueFavoritesRoot
        available={false}
        captured={captured}
        persist={persist}
      />,
    );
    expect(captured.current?.savedFavorites).toEqual([]);
    expect(captured.current?.saveFavorite).toBeUndefined();

    await act(async () => write.resolve(persist.mock.calls[0]?.[0] ?? {}));
    view.rerender(
      <QueueFavoritesRoot
        available
        captured={captured}
        persist={persist}
      />,
    );
    expect(captured.current?.savedFavorites).toEqual([]);
  });

  test("does not update hook state after unmount while persistence is pending", async () => {
    const write = deferred<RuntimeUserPreferences>();
    const persist = vi.fn((_next: RuntimeUserPreferences) => write.promise);
    const captured = captureRef();
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => undefined);
    const view = renderQueueFavorites({ available: true, captured, persist });

    act(() => captured.current?.saveFavorite?.("Pending"));
    await waitFor(() => expect(persist).toHaveBeenCalledOnce());
    view.unmount();
    await act(async () => write.resolve(persist.mock.calls[0]?.[0] ?? {}));

    expect(consoleError.mock.calls.flat().join(" ")).not.toMatch(
      /state update.*unmounted/i,
    );
    consoleError.mockRestore();
  });
});

interface QueueFavoritesRootProps {
  available: boolean;
  captured: { current: ResourceViewContextValue | null };
  persist: (
    preferences: RuntimeUserPreferences,
  ) => Promise<RuntimeUserPreferences>;
}

function renderQueueFavorites(props: QueueFavoritesRootProps) {
  return render(<QueueFavoritesRoot {...props} />);
}

function QueueFavoritesRoot({
  available,
  captured,
  persist,
}: QueueFavoritesRootProps): React.ReactElement {
  return (
    <ModelMetadataProvider metadata={METADATA}>
      <RealQueuePreferences available={available} persist={persist}>
        <ResourceViewProvider scope="local" resource="notes.Note">
          <Capture onValue={(value) => { captured.current = value; }} />
        </ResourceViewProvider>
      </RealQueuePreferences>
    </ModelMetadataProvider>
  );
}

function RealQueuePreferences({
  available,
  children,
  persist,
}: {
  available: boolean;
  children: React.ReactNode;
  persist: (
    preferences: RuntimeUserPreferences,
  ) => Promise<RuntimeUserPreferences>;
}): React.ReactElement {
  const [preferences, setPreferences] = React.useState<RuntimeUserPreferences>({});
  const queue = React.useMemo(
    () => createUserPreferencesPatchQueue({
      persist,
      committed: setPreferences,
    }),
    [available, persist],
  );
  React.useEffect(() => {
    // The queue is intentionally session-scoped; server updates arrive via commits.
    queue.rebase(preferences);
    return () => queue.close();
  }, [queue]);
  const patchPreferences = React.useCallback(
    async (apply: RuntimeUserPreferencesPatch): Promise<void> => {
      if (!available) return;
      await queue.patch(apply);
    },
    [available, queue],
  );
  const runtime = React.useMemo(
    () => ({
      userPreferences: { available, preferences, patchPreferences },
    }),
    [available, patchPreferences, preferences],
  );
  return (
    <AppRuntimeProvider runtime={runtime}>{children}</AppRuntimeProvider>
  );
}

function Capture({
  onValue,
}: {
  onValue: (value: ResourceViewContextValue) => void;
}): null {
  onValue(useResourceView());
  return null;
}

function captureRef(): { current: ResourceViewContextValue | null } {
  return { current: null };
}

function deferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((nextResolve) => {
    resolve = nextResolve;
  });
  return { promise, resolve };
}

interface Deferred<T> {
  promise: Promise<T>;
  resolve: (value: T) => void;
}

function installLocalStorageStub(): void {
  const entries = new Map<string, string>();
  Object.defineProperty(window, "localStorage", {
    configurable: true,
    value: {
      get length() {
        return entries.size;
      },
      clear: () => entries.clear(),
      getItem: (key: string) => entries.get(key) ?? null,
      key: (index: number) => [...entries.keys()][index] ?? null,
      removeItem: (key: string) => {
        entries.delete(key);
      },
      setItem: (key: string, value: string) => {
        entries.set(key, value);
      },
    } satisfies Storage,
  });
}
