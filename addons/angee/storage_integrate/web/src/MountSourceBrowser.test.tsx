// @vitest-environment happy-dom

import {
  cleanup,
  render,
  screen,
} from "@testing-library/react";
import * as React from "react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

const browserMocks = vi.hoisted(() => ({
  supportsManualToken: true,
  variables: null as Record<string, unknown> | null,
}));

vi.mock("@angee/refine", async (importOriginal) => {
  const original = await importOriginal<typeof import("@angee/refine")>();
  return {
    ...original,
    useAuthoredQuery: (
      _document: unknown,
      variables: Record<string, unknown>,
    ) => {
      browserMocks.variables = variables;
      return {
        data: {
          browse_mount_source: {
            location: {
              token: "/srv/shared",
              label: "shared",
              is_navigable: true,
              is_mountable: true,
              blocked_reason: "",
            },
            parent_token: "/srv",
            entries: [
              {
                token: "/srv/shared/child",
                label: "child",
                is_navigable: true,
                is_mountable: true,
                blocked_reason: "",
              },
            ],
            truncated: false,
            supports_manual_token: browserMocks.supportsManualToken,
          },
        },
        fetching: false,
        error: null,
      };
    },
  };
});

import { MountSourceBrowser } from "./MountSourceBrowser";

const onChange = vi.fn();

function Browser({
  value = "",
  readOnly = false,
}: {
  value?: unknown;
  readOnly?: boolean;
}): React.ReactElement {
  return (
    <>
      <span id="mount-source-label">Source folder</span>
      <MountSourceBrowser
        backendClass="local_folder"
        id="mount-source"
        value={value}
        readOnly={readOnly}
        describedBy="mount-help"
        labelledBy="mount-source-label"
        dialogValues={{}}
        onChange={onChange}
      />
      <p id="mount-help">Choose one folder.</p>
    </>
  );
}

describe("MountSourceBrowser", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    onChange.mockClear();
    browserMocks.supportsManualToken = true;
    browserMocks.variables = null;
  });

  afterEach(() => {
    cleanup();
    vi.useRealTimers();
  });

  test("degrades an invalid control value to an empty wire value", () => {
    expect(() =>
      render(<Browser value={{ invalid: true }} />),
    ).not.toThrow();
    expect(browserMocks.variables).toEqual({
      backendClass: "local_folder",
      token: "",
    });
  });

  test("disables every interactive browse control when read-only", () => {
    render(<Browser readOnly />);
    expect(
      (screen.getByPlaceholderText("Enter or paste a source location") as HTMLInputElement)
        .disabled,
    ).toBe(true);
    expect((screen.getByRole("button", { name: "Up" }) as HTMLButtonElement).disabled).toBe(true);
    expect(
      (screen.getByRole("button", { name: "Use this folder" }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
    expect((screen.getByRole("button", { name: /child/ }) as HTMLButtonElement).disabled).toBe(true);
  });

  test("labels the browser group without relabelling its intrinsic action", () => {
    render(<Browser />);
    const group = screen.getByRole("group", { name: "Source folder" });
    const input = screen.getByPlaceholderText("Enter or paste a source location");
    expect(group.getAttribute("aria-describedby")).toBe("mount-help");
    expect(input.id).toBe("mount-source");
    expect(input.getAttribute("aria-label")).toBeNull();
    expect(input.getAttribute("aria-labelledby")).toBe("mount-source-label");
    expect(screen.getByRole("button", { name: "Use this folder" })).toBeTruthy();
  });

  test("keeps the group label when the backend has no manual token input", () => {
    browserMocks.supportsManualToken = false;
    render(<Browser />);
    expect(screen.getByRole("group", { name: "Source folder" })).toBeTruthy();
    expect(screen.queryByPlaceholderText("Enter or paste a source location")).toBeNull();
    expect(screen.getByRole("button", { name: "Use this folder" })).toBeTruthy();
  });
});
