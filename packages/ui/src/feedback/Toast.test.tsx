// @vitest-environment happy-dom

import {
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { AppRuntimeProvider } from "../runtime";
import { describe, expect, test, vi } from "vitest";
import type { ReactElement } from "react";

import { baseIcons } from "../chrome/icon-registry";
import { ToastProvider, useToast } from "./Toast";
import { useRefineNotificationProvider } from "./refine-notification";

describe("ToastProvider", () => {
  test("the toast region is named in the app's language, and in English without one", () => {
    const view = render(
      <AppRuntimeProvider runtime={{ icons: baseIcons }}>
        <ToastProvider><ToastButton /></ToastProvider>
      </AppRuntimeProvider>,
    );
    expect(screen.getByRole("region", { name: "Notifications" })).toBeTruthy();
    view.unmount();

    // A host translator that answers every key of the `ui` namespace with its own words.
    const translated = (key: string, options?: { defaultValue?: string }) =>
      key === "toast.region" ? "Meldungen" : options?.defaultValue ?? key;
    render(
      <AppRuntimeProvider runtime={{ icons: baseIcons, i18n: { language: "de", getFixedT: () => translated } }}>
        <ToastProvider><ToastButton /></ToastProvider>
      </AppRuntimeProvider>,
    );
    expect(screen.getByRole("region", { name: "Meldungen" })).toBeTruthy();
    expect(screen.queryByRole("region", { name: "Notifications" })).toBeNull();
  });

  test("renders a toast fired from a component hook", async () => {
    render(
      <AppRuntimeProvider runtime={{ icons: baseIcons }}>
        <ToastProvider>
          <ToastButton />
        </ToastProvider>
      </AppRuntimeProvider>,
    );

    fireEvent.click(screen.getByRole("button", { name: "Show toast" }));

    expect(await screen.findByText("Import queued")).toBeTruthy();
  });

  test("bridges refine notifications into keyed base toasts", async () => {
    const onCancel = vi.fn();

    render(
      <AppRuntimeProvider runtime={{ icons: baseIcons }}>
        <ToastProvider>
          <RefineNotificationButtons onCancel={onCancel} />
        </ToastProvider>
      </AppRuntimeProvider>,
    );

    fireEvent.click(screen.getByRole("button", { name: "Open notification" }));

    expect(await screen.findByText("Syncing")).toBeTruthy();
    expect(screen.getByText("Still working")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

    expect(onCancel).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole("button", { name: "Open notification" }));
    expect(await screen.findByText("Syncing")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Close notification" }));

    await waitFor(() => {
      expect(screen.queryByText("Syncing")).toBeNull();
    });
  });
});

function ToastButton(): ReactElement {
  const toast = useToast();

  return (
    <button
      type="button"
      onClick={() => {
        toast.info({
          title: "Import queued",
          duration: 0,
        });
      }}
    >
      Show toast
    </button>
  );
}

function RefineNotificationButtons({
  onCancel,
}: {
  onCancel: () => void;
}): ReactElement {
  const notifications = useRefineNotificationProvider();

  return (
    <>
      <button
        type="button"
        onClick={() => {
          notifications.open({
            key: "sync",
            type: "progress",
            message: "Syncing",
            description: "Still working",
            cancelMutation: onCancel,
          });
        }}
      >
        Open notification
      </button>
      <button
        type="button"
        onClick={() => notifications.close("sync")}
      >
        Close notification
      </button>
    </>
  );
}
