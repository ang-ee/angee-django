// @vitest-environment happy-dom

import * as React from "react";
import { act, cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test } from "vitest";

import {
  ResourceViewProvider,
  useResourceView,
  type ResourceViewContextValue,
} from "./resource-view-context";

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

  test("reads favorites from the current resource without clobbering another resource", () => {
    window.localStorage.setItem(
      storageKey("beta"),
      JSON.stringify([{ id: "favorite:beta", label: "Beta" }]),
    );
    const captured: { current: ResourceViewContextValue | null } = {
      current: null,
    };

    const { rerender } = render(
      <FavoriteCapture resource="alpha" onValue={(value) => { captured.current = value; }} />,
    );

    act(() => {
      captured.current?.saveFavorite("Alpha");
    });
    expect(JSON.parse(window.localStorage.getItem(storageKey("alpha")) ?? "[]")).toEqual([
      expect.objectContaining({ label: "Alpha" }),
    ]);

    rerender(
      <FavoriteCapture resource="beta" onValue={(value) => { captured.current = value; }} />,
    );

    expect(captured.current?.savedFavorites).toEqual([
      { id: "favorite:beta", label: "Beta" },
    ]);
    expect(JSON.parse(window.localStorage.getItem(storageKey("beta")) ?? "[]")).toEqual([
      { id: "favorite:beta", label: "Beta" },
    ]);
  });
});

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

function storageKey(resource: string): string {
  return `angee:resource-view:${resource}:favorites`;
}
