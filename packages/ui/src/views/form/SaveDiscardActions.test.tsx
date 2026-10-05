// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { AppRuntimeProvider, createAngeeI18nInstance } from "../../runtime";
import { SaveDiscardActions } from "./SaveDiscardActions";

afterEach(cleanup);

test.each([
  { isDirty: false, alwaysShowSave: false, labels: [] },
  { isDirty: false, alwaysShowSave: true, labels: ["Save"] },
  { isDirty: true, alwaysShowSave: false, labels: ["Discard", "Save"] },
  { isDirty: true, alwaysShowSave: true, labels: ["Discard", "Save"] },
])("dirty=$isDirty, forced=$alwaysShowSave shows the actions in order", ({ isDirty, alwaysShowSave, labels }) => {
  render(<SaveDiscardActions isDirty={isDirty} alwaysShowSave={alwaysShowSave} onDiscard={vi.fn()} onSave={vi.fn()} />);

  expect(screen.queryAllByRole("button").map((button) => button.textContent)).toEqual(labels);
});

test("pending disables both actions and marks Save busy", () => {
  const onDiscard = vi.fn();
  const onSave = vi.fn();
  render(<SaveDiscardActions isDirty pending onDiscard={onDiscard} onSave={onSave} />);
  const discard = screen.getByRole("button", { name: "Discard" });
  const save = screen.getByRole("button", { name: "Save" });

  expect(discard.hasAttribute("disabled")).toBe(true);
  expect(save.hasAttribute("disabled")).toBe(true);
  expect(save.getAttribute("aria-busy")).toBe("true");
  expect(discard.hasAttribute("aria-busy")).toBe(false);
  fireEvent.click(discard);
  fireEvent.click(save);
  expect(onDiscard).not.toHaveBeenCalled();
  expect(onSave).not.toHaveBeenCalled();
});

test("each action calls its own callback", () => {
  const onDiscard = vi.fn();
  const onSave = vi.fn();
  render(<SaveDiscardActions isDirty onDiscard={onDiscard} onSave={onSave} />);

  fireEvent.click(screen.getByRole("button", { name: "Discard" }));
  expect(onDiscard).toHaveBeenCalledTimes(1);
  expect(onSave).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  expect(onSave).toHaveBeenCalledTimes(1);
  expect(onDiscard).toHaveBeenCalledTimes(1);
});

test("blocking Save keeps Discard available", () => {
  const onDiscard = vi.fn();
  const onSave = vi.fn();
  render(<SaveDiscardActions isDirty saveDisabled onDiscard={onDiscard} onSave={onSave} />);

  const discard = screen.getByRole("button", { name: "Discard" });
  const save = screen.getByRole("button", { name: "Save" });
  expect(discard.hasAttribute("disabled")).toBe(false);
  expect(save.hasAttribute("disabled")).toBe(true);
  fireEvent.click(discard);
  fireEvent.click(save);
  expect(onDiscard).toHaveBeenCalledTimes(1);
  expect(onSave).not.toHaveBeenCalled();
});

test.each([
  { saveIntent: "save" as const, label: "Uložit" },
  { saveIntent: "create" as const, label: "Vytvořit" },
])("resolves Discard and $saveIntent labels from runtime i18n", ({ saveIntent, label }) => {
  const i18n = createAngeeI18nInstance({});
  i18n.addResourceBundle("cs", "ui", {
    "form.discard": "Zahodit",
    "form.save": "Uložit",
    "form.create": "Vytvořit",
  });
  void i18n.changeLanguage("cs");
  render(<AppRuntimeProvider runtime={{ i18n }}>
    <SaveDiscardActions isDirty saveIntent={saveIntent} onDiscard={vi.fn()} onSave={vi.fn()} />
  </AppRuntimeProvider>);

  expect(screen.getByRole("button", { name: "Zahodit" })).toBeTruthy();
  expect(screen.getByRole("button", { name: label })).toBeTruthy();
});

test("uses the English Create default without a provider", () => {
  render(<SaveDiscardActions isDirty={false} alwaysShowSave saveIntent="create" onDiscard={vi.fn()} onSave={vi.fn()} />);

  expect(screen.getByRole("button", { name: "Create" })).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Discard" })).toBeNull();
});

test("honours a custom save label ahead of the default intent", () => {
  const i18n = createAngeeI18nInstance({ ui: { "form.save": "Save draft" } });
  render(<AppRuntimeProvider runtime={{ i18n }}>
    <SaveDiscardActions isDirty saveIntent="create" saveLabel={i18n.t("ui:form.save")} onDiscard={vi.fn()} onSave={vi.fn()} />
  </AppRuntimeProvider>);

  expect(screen.getByRole("button", { name: "Save draft" })).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Create" })).toBeNull();
});
