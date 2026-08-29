// @vitest-environment happy-dom

import * as React from "react";
import { act, cleanup, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import type { LiveEvent } from "@refinedev/core";
import {
  ModelMetadataProvider,
  schemaFieldMetadataFromDataResources,
} from "@angee/metadata";
import { testDataResource } from "@angee/metadata/testing";
import {
  AppRuntimeProvider,
  type RuntimeUserPreferences,
} from "@angee/ui/runtime";
import {
  APP_RAIL_PREFERENCES_KEY,
  useAppRailPreferences,
} from "@angee/ui/chrome/app-rail-preferences";
import {
  ResourceViewProvider,
  useResourceView,
  type ResourceViewContextValue,
} from "@angee/ui/views/resource-view-context";

import {
  ANONYMOUS_AUTH,
  AuthStateProvider,
  currentUserToAuthState,
  type AuthState,
  UserPreferencesProvider,
  useUserPreferences,
} from "./auth";

const liveHarness = vi.hoisted(() => ({
  invalidateAuthStore: vi.fn(async () => undefined),
  requestIdentityRefetch: null as (() => void) | null,
  deliverIdentityRefetch: null as (() => void) | null,
  persist: vi.fn(),
  subscription: null as Record<string, unknown> | null,
  subscribeCount: 0,
  unsubscribeCount: 0,
}));

vi.mock("@refinedev/core", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@refinedev/core")>();
  const ReactModule = await import("react");
  return {
    ...actual,
    useInvalidateAuthStore: () => liveHarness.invalidateAuthStore,
    useSubscription: (props: Record<string, unknown>) => {
      ReactModule.useEffect(() => {
        if (props.enabled !== true) return undefined;
        liveHarness.subscribeCount += 1;
        liveHarness.subscription = props;
        return () => {
          liveHarness.unsubscribeCount += 1;
          if (liveHarness.subscription === props) {
            liveHarness.subscription = null;
          }
        };
      }, [props.enabled]);
    },
  };
});

vi.mock("@angee/refine", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@angee/refine")>();
  return {
    ...actual,
    useAuthoredMutation: () => [
      (variables: { preferences: RuntimeUserPreferences }) =>
        liveHarness.persist(variables),
      { error: null, fetching: false },
    ],
  };
});

const METADATA = schemaFieldMetadataFromDataResources([
  testDataResource("notes.Note"),
]);
const FAVORITES_KEY = "resource-view.favorites";
const FAVORITES_VERSION = 1;
const USER = {
  id: "usr_actor",
  username: "actor",
  firstName: "Preference",
  lastName: "Actor",
  email: "actor@example.com",
  isStaff: false,
  isActive: true,
  roleRefs: [],
};

describe("live user preferences", () => {
  beforeEach(() => {
    installLocalStorageStub();
    liveHarness.requestIdentityRefetch = null;
    liveHarness.deliverIdentityRefetch = null;
    liveHarness.invalidateAuthStore.mockReset();
    liveHarness.invalidateAuthStore.mockImplementation(async () => {
      liveHarness.requestIdentityRefetch?.();
    });
    liveHarness.persist.mockReset();
    liveHarness.persist.mockImplementation(
      async ({ preferences }: { preferences: RuntimeUserPreferences }) =>
        preferenceMutationResult(preferences),
    );
    liveHarness.subscription = null;
    liveHarness.subscribeCount = 0;
    liveHarness.unsubscribeCount = 0;
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  test("rebases rail and favorites together from one remote delivery", () => {
    const captured = captureRef();
    renderPreferences(captured, preferenceDocument("Local", true));

    expect(liveHarness.subscription).toMatchObject({
      enabled: true,
      meta: { dataProviderName: "public" },
      params: { models: ["iam.User"] },
      types: ["updated"],
    });

    act(() => emitPreferences(preferenceDocument("Other", false), "usr_other"));
    expect(captured.current?.view.savedFavorites.map(({ label }) => label))
      .toEqual(["Local"]);

    act(() => emitPreferences(preferenceDocument("Remote", false)));

    expect(captured.current?.rail.railPreferences.expanded).toBe(false);
    expect(captured.current?.view.savedFavorites.map(({ label }) => label))
      .toEqual(["Remote"]);
  });

  test("keeps an in-flight remote rebase and bases the next surface write on it", async () => {
    const first = deferred<ReturnType<typeof preferenceMutationResult>>();
    liveHarness.persist
      .mockImplementationOnce(() => first.promise)
      .mockImplementation(
        async ({ preferences }: { preferences: RuntimeUserPreferences }) =>
          preferenceMutationResult(preferences),
      );
    const captured = captureRef();
    renderPreferences(captured, preferenceDocument("Initial", true));

    act(() => captured.current?.rail.setRailPreferences({
      order: ["local"],
      defaultItemId: "local",
      expanded: true,
    }));
    await waitFor(() => expect(liveHarness.persist).toHaveBeenCalledOnce());

    const remote = preferenceDocument("Remote", false);
    act(() => emitPreferences(remote));
    await act(async () => {
      first.resolve(preferenceMutationResult(
        liveHarness.persist.mock.calls[0]?.[0].preferences,
      ));
      await first.promise;
    });

    await waitFor(() => {
      expect(captured.current?.rail.railPreferences.expanded).toBe(false);
      expect(captured.current?.view.savedFavorites.map(({ label }) => label))
        .toEqual(["Remote"]);
    });

    act(() => captured.current?.view.saveFavorite?.("After remote"));
    await waitFor(() => expect(liveHarness.persist).toHaveBeenCalledTimes(2));
    const submitted = liveHarness.persist.mock.calls[1]?.[0]
      .preferences as RuntimeUserPreferences;
    expect(submitted[APP_RAIL_PREFERENCES_KEY]).toEqual(
      remote[APP_RAIL_PREFERENCES_KEY],
    );
    expect(favoriteLabels(submitted)).toEqual(["Remote", "After remote"]);
  });

  test("commits a local write before the identity refresh rebases to server truth", async () => {
    const captured = captureRef();
    const committed = preferenceDocument("Committed", false);
    const serverTruth = preferenceDocument("Server truth", true);
    liveHarness.persist.mockResolvedValue(preferenceMutationResult(committed));
    render(
      <IdentityRefetchRoot
        captured={captured}
        initial={preferenceDocument("Initial", true)}
        refreshed={serverTruth}
      />,
    );

    await act(async () => {
      await captured.current?.preferences.patchPreferences(
        () => preferenceDocument("Requested", false),
      );
    });

    expect(liveHarness.invalidateAuthStore).toHaveBeenCalledOnce();
    expect(favoriteLabels(captured.current?.preferences.preferences ?? {}))
      .toEqual(["Committed"]);
    expect(liveHarness.deliverIdentityRefetch).not.toBeNull();

    act(() => liveHarness.deliverIdentityRefetch?.());

    await waitFor(() => {
      expect(favoriteLabels(captured.current?.preferences.preferences ?? {}))
        .toEqual(["Server truth"]);
      expect(captured.current?.rail.railPreferences.expanded).toBe(true);
    });
  });

  test("delivers after StrictMode's simulated subscription remount", () => {
    const captured = captureRef();
    renderPreferences(
      captured,
      preferenceDocument("Initial", true),
      { strict: true },
    );

    expect(liveHarness.subscribeCount).toBeGreaterThanOrEqual(2);
    expect(liveHarness.unsubscribeCount).toBeGreaterThanOrEqual(1);
    act(() => emitPreferences(preferenceDocument("After remount", false)));

    expect(captured.current?.view.savedFavorites.map(({ label }) => label))
      .toEqual(["After remount"]);
  });

  test("wires no preference subscription for an anonymous session", () => {
    const captured = captureRef();
    renderPreferencesWithAuth(captured, ANONYMOUS_AUTH);

    expect(liveHarness.subscribeCount).toBe(0);
    expect(liveHarness.subscription).toBeNull();
    expect(captured.current?.preferences.available).toBe(false);
  });
});

interface CapturedPreferences {
  preferences: ReturnType<typeof useUserPreferences>;
  rail: ReturnType<typeof useAppRailPreferences>;
  view: ResourceViewContextValue;
}

function renderPreferences(
  captured: { current: CapturedPreferences | null },
  preferences: RuntimeUserPreferences,
  options: { strict?: boolean } = {},
): void {
  const auth = currentUserToAuthState({ ...USER, preferences });
  const tree = <PreferencesRoot auth={auth} captured={captured} />;
  render(options.strict ? <React.StrictMode>{tree}</React.StrictMode> : tree);
}

function renderPreferencesWithAuth(
  captured: { current: CapturedPreferences | null },
  auth: AuthState,
): void {
  render(<PreferencesRoot auth={auth} captured={captured} />);
}

function PreferencesRoot({
  auth,
  captured,
}: {
  auth: AuthState;
  captured: { current: CapturedPreferences | null };
}): React.ReactElement {
  return (
    <AuthStateProvider auth={auth}>
      <UserPreferencesProvider dataProviderName="public">
        <PreferenceRuntime>
          <ModelMetadataProvider metadata={METADATA}>
            <ResourceViewProvider scope="local" resource="notes.Note">
              <Capture captured={captured} />
            </ResourceViewProvider>
          </ModelMetadataProvider>
        </PreferenceRuntime>
      </UserPreferencesProvider>
    </AuthStateProvider>
  );
}

function IdentityRefetchRoot({
  captured,
  initial,
  refreshed,
}: {
  captured: { current: CapturedPreferences | null };
  initial: RuntimeUserPreferences;
  refreshed: RuntimeUserPreferences;
}): React.ReactElement {
  const [preferences, setPreferences] = React.useState(initial);
  React.useEffect(() => {
    liveHarness.requestIdentityRefetch = () => {
      liveHarness.deliverIdentityRefetch = () => setPreferences(refreshed);
    };
    return () => {
      liveHarness.requestIdentityRefetch = null;
      liveHarness.deliverIdentityRefetch = null;
    };
  }, [refreshed]);
  return (
    <PreferencesRoot
      auth={currentUserToAuthState({ ...USER, preferences })}
      captured={captured}
    />
  );
}

function PreferenceRuntime({ children }: { children: React.ReactNode }) {
  const userPreferences = useUserPreferences();
  return (
    <AppRuntimeProvider runtime={{ userPreferences }}>
      {children}
    </AppRuntimeProvider>
  );
}

function Capture({
  captured,
}: {
  captured: { current: CapturedPreferences | null };
}): null {
  captured.current = {
    preferences: useUserPreferences(),
    rail: useAppRailPreferences(),
    view: useResourceView(),
  };
  return null;
}

function emitPreferences(
  preferences: RuntimeUserPreferences,
  id = USER.id,
): void {
  const onLiveEvent = liveHarness.subscription?.onLiveEvent as
    | ((event: LiveEvent) => void)
    | undefined;
  if (!onLiveEvent) throw new Error("Preference subscription was not opened.");
  onLiveEvent({
    channel: "angee/user-preferences/test",
    type: "updated",
    payload: {
      id,
      model: "iam.User",
      changedFields: ["preferences"],
      changedValues: { preferences },
    },
    date: new Date(0),
    meta: { dataProviderName: "public" },
  });
}

function preferenceDocument(
  favoriteLabel: string,
  expanded: boolean,
): RuntimeUserPreferences {
  return {
    [APP_RAIL_PREFERENCES_KEY]: {
      order: ["notes"],
      defaultItemId: "notes",
      expanded,
    },
    [FAVORITES_KEY]: {
      version: FAVORITES_VERSION,
      models: {
        "notes.Note": [{
          id: `favorite:${favoriteLabel.toLowerCase().replaceAll(" ", "-")}`,
          label: favoriteLabel,
        }],
      },
    },
  };
}

function favoriteLabels(preferences: RuntimeUserPreferences): string[] {
  const document = preferences[FAVORITES_KEY] as {
    models: Record<string, readonly { label: string }[]>;
  };
  return document.models["notes.Note"]?.map(({ label }) => label) ?? [];
}

function preferenceMutationResult(preferences: RuntimeUserPreferences) {
  return {
    update_preferences: {
      ...USER,
      preferences,
    },
  };
}

function captureRef(): { current: CapturedPreferences | null } {
  return { current: null };
}

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
