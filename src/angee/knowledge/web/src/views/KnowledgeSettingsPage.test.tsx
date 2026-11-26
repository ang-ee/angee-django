// @vitest-environment happy-dom

import { render } from "@testing-library/react";
import type { ReactNode } from "react";
import type { ActionDescriptor } from "@angee/ui";
import type { AuthoredMutationOptions } from "@angee/refine";
import type { DocumentType } from "@angee/gql/console";
import { beforeEach, expect, test, vi } from "vitest";

import { KnowledgeCreateVaultFrom } from "../data/documents";

type CloneData = DocumentType<typeof KnowledgeCreateVaultFrom>;
const mocks = vi.hoisted(() => ({
  submit: null as ActionDescriptor["submit"] | null,
  action: null as ActionDescriptor | null,
  options: null as AuthoredMutationOptions<CloneData> | null,
  mutation: vi.fn(),
  canonicalize: vi.fn(),
  invalidates: vi.fn(),
}));

vi.mock("@angee/metadata", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@angee/metadata")>()),
  useCanonicalResourceModelLabels: mocks.canonicalize,
  useResourceInvalidates: mocks.invalidates,
}));

vi.mock("@angee/refine", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@angee/refine")>()),
  useAuthoredMutation: (_document: unknown, options: AuthoredMutationOptions<CloneData>) => {
    mocks.options = options;
    return [mocks.mutation, { fetching: false, error: null }];
  },
}));

vi.mock("@angee/ui", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@angee/ui")>()),
  Action: (action: ActionDescriptor) => { mocks.action = action; mocks.submit = action.submit; return null; },
  Column: () => null,
  Field: () => null,
  Form: ({ children }: { children?: ReactNode }) => <>{children}</>,
  List: ({ children }: { children?: ReactNode }) => <>{children}</>,
  DrawerResourceList: ({ children }: { children?: ReactNode }) => <>{children}</>,
}));

import { KnowledgeSettingsPage } from "./KnowledgeSettingsPage";

beforeEach(() => {
  mocks.submit = null;
  mocks.action = null;
  mocks.options = null;
  mocks.mutation.mockReset().mockResolvedValue({ create_vault_from: { ok: false, message: "Try again" } });
  mocks.canonicalize.mockReset().mockReturnValue(["knowledge.vault", "knowledge.page", "knowledge.markdownpage"]);
  mocks.invalidates.mockReset().mockReturnValue([]);
});

async function submit(name = "Copy", template = "vlt_template") {
  if (!mocks.submit) throw new Error("Clone action was not registered");
  return mocks.submit({ name }, { record: { id: template }, selectedIds: [] }, { errorNotification: false });
}

/** The key each clone request carried, in order. */
const sentKeys = () => mocks.mutation.mock.calls.map(([variables]) => (variables as { idempotency_key: string }).idempotency_key);

test("a lost answer keeps the key for the same clone; a refused result releases it", async () => {
  render(<KnowledgeSettingsPage />);
  expect(mocks.action).toMatchObject({ id: "create-from-template", placement: "menu" });
  mocks.mutation.mockRejectedValueOnce(new Error("Offline"));
  await expect(submit(" Lost ")).rejects.toThrow("Offline");
  expect(await submit("Lost")).toMatchObject({ ok: false });
  await submit("Lost");
  const [lost, retried, afterRefusal] = sentKeys();
  expect(retried).toBe(lost);
  expect(afterRefusal).not.toBe(retried);
  expect(mocks.mutation).toHaveBeenNthCalledWith(1, { template: "vlt_template", name: "Lost", owned: true, idempotency_key: lost },
    { errorNotification: false });
});

test("success releases the key so another clone gets a new one", async () => {
  mocks.mutation.mockResolvedValue({ create_vault_from: { ok: true, id: "vlt_created", message: "Created" } });
  render(<KnowledgeSettingsPage />);
  expect(await submit("Created")).toEqual({ ok: true, id: "vlt_created", message: "Created" });
  await submit("Created");
  const [first, second] = sentKeys();
  expect(second).not.toBe(first);
});

test("changing the name or template starts a new creation request", async () => {
  mocks.mutation.mockRejectedValue(new Error("Offline"));
  render(<KnowledgeSettingsPage />);
  await expect(submit("Kept")).rejects.toThrow("Offline");
  await expect(submit("Changed")).rejects.toThrow("Offline");
  await expect(submit("Changed", "vlt_other")).rejects.toThrow("Offline");
  expect(new Set(sentKeys()).size).toBe(3);
});

test("successful clones invalidate metadata-canonical model names", () => {
  render(<KnowledgeSettingsPage />);
  expect(mocks.canonicalize).toHaveBeenCalledWith(["knowledge.Vault", "knowledge.Page", "knowledge.MarkdownPage"]);
  const canonical = ["knowledge.vault", "knowledge.page", "knowledge.markdownpage"];
  expect(mocks.invalidates).toHaveBeenCalledWith(canonical);
  expect(mocks.options?.invalidateModels).toEqual(canonical);
  expect(mocks.options?.shouldInvalidate?.({
    create_vault_from: { ok: true, id: "vlt_created", message: "Created", validation_errors: null },
  }, {})).toBe(true);
  expect(mocks.options?.shouldInvalidate?.({
    create_vault_from: { ok: false, id: null, message: "Try again", validation_errors: null },
  }, {})).toBe(false);
});
