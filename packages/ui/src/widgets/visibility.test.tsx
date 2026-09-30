// @vitest-environment happy-dom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { ToastProvider } from "../feedback";
import { AppRuntimeProvider } from "../runtime";
import { createUiTestProviders } from "../testing";
import { VisibilityControl } from "./visibility";

vi.mock("@tanstack/react-router", () => ({ useNavigate: () => vi.fn() }));

const { Provider, clearClients } = createUiTestProviders();
const options = [
  { value: "private", label: "Private" },
  { value: "team", label: "Team" },
  { value: "public", label: "Public" },
];
afterEach(() => { cleanup(); clearClients(); });

test("offers returned choices and leaves the acknowledged label unchanged on failure", async () => {
  const onSelect = vi.fn(async () => ({ ok: false, message: "Permission changed" }));
  render(<Provider><ToastProvider><VisibilityControl value="TEAM" options={options}
    allowedValues={["private", "public"]} onSelect={onSelect} /></ToastProvider></Provider>);
  fireEvent.click(screen.getByRole("button", { name: "Visibility" }));
  expect(screen.queryByRole("menuitem", { name: /Team/ })).toBeNull();
  fireEvent.click(await screen.findByRole("menuitem", { name: "Private" }));
  await waitFor(() => expect(onSelect).toHaveBeenCalledExactlyOnceWith("private"));
  expect(screen.getByRole("button", { name: "Visibility" }).textContent).toContain("Team");
});

test("the server's allowed values alone determine which options are offered", async () => {
  const onSelect = vi.fn();
  render(<Provider><ToastProvider><VisibilityControl value="team" options={options}
    allowedValues={["team", "public"]} onSelect={onSelect} /></ToastProvider></Provider>);
  expect(screen.queryByRole("button", { name: /Private/ })).toBeNull();
  expect(onSelect).not.toHaveBeenCalled();
  expect((screen.getByRole("button", { name: "Change visibility to Public" }) as HTMLButtonElement).disabled).toBe(false);
});

test("view-as previews cannot invoke audience verbs", () => {
  const onSelect = vi.fn();
  render(<Provider><ToastProvider><AppRuntimeProvider runtime={{ auth: {
    user: { id: "u1", name: "Person" }, status: "authenticated", hasRole: () => false,
    viewAs: { viewAs: { userId: "u1" }, currentUser: { id: "u1", name: "Person" }, realUser: null,
      viewablePeople: [], enter: vi.fn(), exit: vi.fn() },
  } }}><VisibilityControl value="private" options={options} allowedValues={["team"]} onSelect={onSelect} />
  </AppRuntimeProvider></ToastProvider></Provider>);
  expect((screen.getByRole("button", { name: "Change visibility to Team" }) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "Change visibility to Team" }));
  expect(onSelect).not.toHaveBeenCalled();
});

test("one permitted destination runs directly from the label", async () => {
  const onSelect = vi.fn(async () => ({ ok: true, message: "Audience changed" }));
  render(<Provider><ToastProvider><VisibilityControl value="team" options={options.slice(0, 2)}
    allowedValues={["private", "team"]} onSelect={onSelect} /></ToastProvider></Provider>);
  fireEvent.click(screen.getByRole("button", { name: "Change visibility to Private" }));
  await waitFor(() => expect(onSelect).toHaveBeenCalledExactlyOnceWith("private"));
  expect(screen.queryByRole("menu")).toBeNull();
});

test("shows the server's audience label without exposing a transition formula", () => {
  render(<Provider><ToastProvider><VisibilityControl value="team" options={options.slice(0, 2)}
    audienceLabel="Project participants" allowedValues={["private", "team"]} onSelect={vi.fn()} />
  </ToastProvider></Provider>);
  const button = screen.getByRole("button", { name: "Change visibility to Private" });
  expect(button.textContent).toContain("Private");
  expect(screen.getByText("Project participants")).toBeTruthy();
  expect(screen.getByText("Project participants").closest("button")).toBeNull();
});
