// @vitest-environment happy-dom

import { afterEach, describe, expect, test } from "vitest";

import { browserLocalStorage } from "./browser-storage";

const originalLocalStorage = Object.getOwnPropertyDescriptor(
  window,
  "localStorage",
);

afterEach(() => {
  if (originalLocalStorage) {
    Object.defineProperty(window, "localStorage", originalLocalStorage);
  }
});

describe("browserLocalStorage", () => {
  test("returns browser localStorage when it implements the Storage methods", () => {
    const storage = {
      getItem: () => null,
      setItem: () => undefined,
      removeItem: () => undefined,
    } as unknown as Storage;
    Object.defineProperty(window, "localStorage", {
      configurable: true,
      value: storage,
    });

    expect(browserLocalStorage()).toBe(storage);
  });

  test("returns null when localStorage is present but not Storage-shaped", () => {
    Object.defineProperty(window, "localStorage", {
      configurable: true,
      value: {},
    });

    expect(browserLocalStorage()).toBeNull();
  });
});
