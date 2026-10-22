// @vitest-environment happy-dom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import * as React from "react";
import { afterEach, expect, test, vi } from "vitest";

import { ModalsHost, ToastProvider } from "../feedback";
import { createUiTestProviders } from "../testing";
import { ReportPage } from "./ReportPage";

const { Provider, clearClients } = createUiTestProviders({
  queryClientConfig: { defaultOptions: { mutations: { retry: false }, queries: { retry: false } } },
});

afterEach(() => { cleanup(); clearClients(); });

function renderPage(children: React.ReactElement): void {
  render(<Provider><ModalsHost><ToastProvider>{children}</ToastProvider></ModalsHost></Provider>);
}

test("a configured run shows its subject header, views and page verbs", async () => {
  const refresh = vi.fn();
  renderPage(
    <ReportPage
      title="Trial balance"
      crumbs="Accounting · Reports"
      facts={["Acme", "EUR", "Jan 1 – Oct 20, 2026"]}
      description="Posted balances only."
      status={{ label: "Balanced", tone: "success" }}
      views={[{ id: "summary", label: "Summary", href: "/a", active: true }, { id: "details", label: "Details", href: "/b" }]}
      actions={[{ id: "refresh", label: "Refresh", run: refresh }]}
      metrics={[{ label: "Debit", numericValue: 10 }]}
    >
      <div>rows</div>
    </ReportPage>,
  );
  expect(screen.getByRole("heading", { level: 1, name: "Trial balance" })).toBeTruthy();
  expect(screen.getByText("Acme")).toBeTruthy();
  expect(screen.getByText("Balanced")).toBeTruthy();
  const views = screen.getByRole("navigation", { name: "Report views" });
  expect(views.querySelector("[aria-current]")?.textContent).toBe("Summary");
  expect(screen.getByText("rows")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
  await waitFor(() => expect(refresh).toHaveBeenCalledOnce());
});

test("an unconfigured run opens its parameter form and offers it from the empty state", async () => {
  const submit = vi.fn();
  renderPage(
    <ReportPage
      title="Aging"
      configured={false}
      empty={{ title: "Choose a company and a cutoff date." }}
      parameters={{ id: "parameters", label: "Report parameters", args: [{ name: "asOf", label: "As of", widget: "date" }], submit }}
    >
      <div>rows</div>
    </ReportPage>,
  );
  const dialog = await screen.findByRole("dialog", { name: "Report parameters" });
  expect(screen.queryByText("rows")).toBeNull();
  fireEvent.keyDown(dialog, { key: "Escape" });
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(screen.getByText("Choose a company and a cutoff date.")).toBeTruthy();
  // The empty state's verb and the toolbar's both open the same form.
  fireEvent.click(screen.getAllByRole("button", { name: "Report parameters" })[0]!);
  expect(await screen.findByRole("dialog", { name: "Report parameters" })).toBeTruthy();
});
