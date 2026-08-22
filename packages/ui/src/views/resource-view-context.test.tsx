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
} from "../runtime";
import {
  ResourceViewProvider,
  useResourceView,
  type ResourceViewContextValue,
} from "./resource-view-context";
import {
  RESOURCE_VIEW_FAVORITES_PREFERENCES_KEY,
  RESOURCE_VIEW_FAVORITES_VERSION,
} from "./resource-view-favorites";

const METADATA = schemaFieldMetadataFromDataResources([
  testDataResource("notes.Note"),
  testDataResource("tasks.Task"),
]);

// Vitest's happy-dom global copy does not expose `window.localStorage` (the
// lookup falls through to Node's experimental accessor), so the test installs
// a deterministic in-memory Storage.
function installLocalStorageStub(): Storage {
  const entries = new Map<string, string>();
  const storage: Storage = {
    get length() {
      return entries.size;
    },
    clear: () => entries.clear(),
    getItem: (key) => entries.get(key) ?? null,
    key: (index) => [...entries.keys()][index] ?? null,
    removeItem: (key) => {
      entries.delete(key);
    },
    setItem: (key, value) => {
      entries.set(key, value);
    },
  };
  Object.defineProperty(window, "localStorage", {
    configurable: true,
    value: storage,
  });
  return storage;
}

describe("ResourceViewProvider favorites", () => {
  beforeEach(() => {
    installLocalStorageStub();
  });
  afterEach(() => {
    cleanup();
    window.localStorage.clear();
  });

  test("reads versioned server favorites by canonical model label", () => {
    const captured = captureRef();
    renderFavoriteHarness({
      captured,
      resource: "Note",
      preferences: favoritesPreferences({
        "notes.Note": [{ id: "favorite:server", label: "Server" }],
        "tasks.Task": [{ id: "favorite:task", label: "Task" }],
      }),
    });

    expect(captured.current?.savedFavorites).toEqual([
      { id: "favorite:server", label: "Server" },
    ]);
    expect(captured.current?.saveFavorite).toBeTypeOf("function");
  });

  test("rejects an unknown preferences schema version", () => {
    const captured = captureRef();
    const persist = vi.fn(async () => undefined);
    renderFavoriteHarness({
      captured,
      persist,
      resource: "notes.Note",
      preferences: {
        [RESOURCE_VIEW_FAVORITES_PREFERENCES_KEY]: {
          version: RESOURCE_VIEW_FAVORITES_VERSION + 1,
          models: {
            "notes.Note": [{ id: "favorite:stale", label: "Stale" }],
          },
        },
      },
    });

    expect(captured.current?.savedFavorites).toEqual([]);
    expect(captured.current?.saveFavorite).toBeUndefined();
    expect(persist).not.toHaveBeenCalled();
  });

  test("rejects malformed entries in the versioned preferences document", () => {
    const captured = captureRef();
    renderFavoriteHarness({
      captured,
      resource: "notes.Note",
      preferences: favoritesPreferences({
        "notes.Note": [{ id: 42, label: "Malformed" }],
      }),
    });

    expect(captured.current?.savedFavorites).toEqual([]);
  });

  test("makes favorites unavailable to anonymous sessions", () => {
    window.localStorage.setItem(
      legacyStorageKey("Note"),
      JSON.stringify([{ id: "favorite:local", label: "Local" }]),
    );
    const captured = captureRef();
    renderFavoriteHarness({
      available: false,
      captured,
      resource: "notes.Note",
      preferences: favoritesPreferences({
        "notes.Note": [{ id: "favorite:private", label: "Private" }],
      }),
    });

    expect(captured.current?.savedFavorites).toEqual([]);
    expect(captured.current?.saveFavorite).toBeUndefined();
  });

  test("imports every legacy model once on the first authenticated write", async () => {
    window.localStorage.setItem(
      legacyStorageKey("Note"),
      JSON.stringify([{ id: "favorite:legacy", label: "Legacy" }]),
    );
    window.localStorage.setItem(
      legacyStorageKey("Task"),
      JSON.stringify([{ id: "favorite:task", label: "Task" }]),
    );
    const captured = captureRef();
    let committed: RuntimeUserPreferences = {};
    const persist = vi.fn(async (next: RuntimeUserPreferences) => {
      committed = next;
    });
    renderFavoriteHarness({
      captured,
      persist,
      preferences: favoritesPreferences({
        "notes.Note": [{ id: "favorite:server", label: "Server" }],
      }),
      resource: "notes.Note",
    });

    act(() => captured.current?.saveFavorite?.("New"));
    await waitFor(() => expect(persist).toHaveBeenCalledOnce());

    expect(committed).toEqual(favoritesPreferences({
      "notes.Note": [
        { id: "favorite:server", label: "Server" },
        { id: "favorite:legacy", label: "Legacy" },
        expect.objectContaining({ id: "favorite:new", label: "New" }),
      ],
      "tasks.Task": [{ id: "favorite:task", label: "Task" }],
    }));
    expect(window.localStorage.getItem(legacyStorageKey("Note"))).toBeNull();
    expect(window.localStorage.getItem(legacyStorageKey("Task"))).toBeNull();

    window.localStorage.setItem(
      legacyStorageKey("Note"),
      JSON.stringify([{ id: "favorite:stale", label: "Stale" }]),
    );
    act(() => captured.current?.saveFavorite?.("Second"));
    await waitFor(() => expect(persist).toHaveBeenCalledTimes(2));
    const document = committed[RESOURCE_VIEW_FAVORITES_PREFERENCES_KEY] as {
      models: Record<string, readonly { label: string }[]>;
    };
    expect(document.models["notes.Note"]?.map(({ label }) => label)).toEqual([
      "Server",
      "Legacy",
      "New",
      "Second",
    ]);
    expect(window.localStorage.getItem(legacyStorageKey("Note"))).not.toBeNull();
  });

  test("rolls an optimistic favorite back when persistence fails", async () => {
    window.localStorage.setItem(
      legacyStorageKey("Note"),
      JSON.stringify([{ id: "favorite:legacy", label: "Legacy" }]),
    );
    const captured = captureRef();
    const persist = vi.fn(async () => {
      throw new Error("preferences unavailable");
    });
    renderFavoriteHarness({
      captured,
      persist,
      resource: "notes.Note",
      preferences: favoritesPreferences({
        "notes.Note": [{ id: "favorite:retained", label: "Retained" }],
      }),
    });

    act(() => captured.current?.saveFavorite?.("Optimistic"));
    expect(captured.current?.savedFavorites.map(({ label }) => label)).toEqual([
      "Retained",
      "Legacy",
      "Optimistic",
    ]);
    await waitFor(() => {
      expect(captured.current?.savedFavorites).toEqual([
        { id: "favorite:retained", label: "Retained" },
      ]);
    });
    expect(window.localStorage.getItem(legacyStorageKey("Note"))).not.toBeNull();
  });
});

interface FavoriteHarnessOptions {
  available?: boolean;
  captured: { current: ResourceViewContextValue | null };
  persist?: (preferences: RuntimeUserPreferences) => Promise<void>;
  preferences?: RuntimeUserPreferences;
  resource: string;
}

function renderFavoriteHarness({
  available = true,
  captured,
  persist,
  preferences = {},
  resource,
}: FavoriteHarnessOptions): void {
  render(
    <PreferencesHarness
      available={available}
      initialPreferences={preferences}
      persist={persist}
    >
      <FavoriteCapture
        resource={resource}
        onValue={(value) => { captured.current = value; }}
      />
    </PreferencesHarness>,
  );
}

function PreferencesHarness({
  available,
  children,
  initialPreferences,
  persist = async () => undefined,
}: {
  available: boolean;
  children: React.ReactNode;
  initialPreferences: RuntimeUserPreferences;
  persist?: (preferences: RuntimeUserPreferences) => Promise<void>;
}): React.ReactElement {
  const current = React.useRef(initialPreferences);
  const [preferences, setPreferences] = React.useState(initialPreferences);
  const patchPreferences = React.useCallback(
    async (patch: RuntimeUserPreferencesPatch): Promise<void> => {
      const next = patch(current.current);
      await persist(next);
      current.current = next;
      setPreferences(next);
    },
    [persist],
  );
  const runtime = React.useMemo(
    () => ({
      userPreferences: { available, preferences, patchPreferences },
    }),
    [available, patchPreferences, preferences],
  );
  return (
    <ModelMetadataProvider metadata={METADATA}>
      <AppRuntimeProvider runtime={runtime}>{children}</AppRuntimeProvider>
    </ModelMetadataProvider>
  );
}

function FavoriteCapture({
  onValue,
  resource,
}: {
  onValue: (value: ResourceViewContextValue) => void;
  resource: string;
}): React.ReactElement {
  return (
    <ResourceViewProvider scope="local" resource={resource}>
      <Capture onValue={onValue} />
    </ResourceViewProvider>
  );
}

function Capture({
  onValue,
}: {
  onValue: (value: ResourceViewContextValue) => void;
}): null {
  const value = useResourceView();
  onValue(value);
  return null;
}

function captureRef(): { current: ResourceViewContextValue | null } {
  return { current: null };
}

function favoritesPreferences(
  models: Readonly<Record<string, readonly unknown[]>>,
): RuntimeUserPreferences {
  return {
    [RESOURCE_VIEW_FAVORITES_PREFERENCES_KEY]: {
      version: RESOURCE_VIEW_FAVORITES_VERSION,
      models,
    },
  };
}

function legacyStorageKey(resource: string): string {
  return `angee:resource-view:${resource}:favorites`;
}
