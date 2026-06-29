// @vitest-environment happy-dom

import type { Row } from "@angee/resources";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import {
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";
import * as React from "react";
import { afterEach, beforeAll, describe, expect, test, vi } from "vitest";

import { ModalsHost, ToastProvider } from "../feedback";
import { RecordActionBar } from "./RecordActionBar";

describe("RecordActionBar", () => {
  beforeAll(() => {
    Element.prototype.getAnimations ??= () => [];
  });

  afterEach(() => {
    cleanup();
  });

  test("clears trigger loading after a run action resolves in StrictMode", async () => {
    let resolveAction!: () => void;
    const run = vi.fn(
      () =>
        new Promise<void>((resolve) => {
          resolveAction = resolve;
        }),
    );

    renderActionBar(
      <React.StrictMode>
        <RecordActionBar
          record={record}
          actions={[{ id: "sync", label: "Sync", run }]}
          applyPatch={vi.fn()}
          reload={vi.fn()}
        />
      </React.StrictMode>,
    );

    fireEvent.click(screen.getByRole("button", { name: "Actions" }));
    fireEvent.click(await screen.findByRole("menuitem", { name: "Sync" }));

    await waitFor(() => expect(run).toHaveBeenCalledTimes(1));
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Actions" }).getAttribute("aria-busy"),
      ).toBe("true"),
    );

    resolveAction();

    await waitFor(() => {
      const trigger = screen.getByRole("button", { name: "Actions" });
      expect(trigger.getAttribute("aria-busy")).toBeNull();
      expect((trigger as HTMLButtonElement).disabled).toBe(false);
    });
  });

  test("clears trigger loading after a run action rejects", async () => {
    const run = vi.fn(async () => {
      throw new Error("Provider rejected the authorization code.");
    });

    renderActionBar(
      <React.StrictMode>
        <RecordActionBar
          record={record}
          actions={[{ id: "sync", label: "Sync", run }]}
          applyPatch={vi.fn()}
          reload={vi.fn()}
        />
      </React.StrictMode>,
    );

    fireEvent.click(screen.getByRole("button", { name: "Actions" }));
    fireEvent.click(await screen.findByRole("menuitem", { name: "Sync" }));

    expect(
      await screen.findAllByText("Provider rejected the authorization code."),
    ).not.toHaveLength(0);
    await waitFor(() => {
      const trigger = screen.getByRole("button", { name: "Actions" });
      expect(trigger.getAttribute("aria-busy")).toBeNull();
      expect((trigger as HTMLButtonElement).disabled).toBe(false);
    });
  });
});

const record: Row = { id: "note-1" };

function renderActionBar(children: React.ReactElement): void {
  const queryClient = new QueryClient({
    defaultOptions: {
      mutations: { retry: false },
      queries: { retry: false },
    },
  });
  render(
    <QueryClientProvider client={queryClient}>
      <ModalsHost>
        <ToastProvider>{children}</ToastProvider>
      </ModalsHost>
    </QueryClientProvider>,
  );
}
