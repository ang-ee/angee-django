// @vitest-environment happy-dom

import { cleanup, render } from "@testing-library/react";
import type { ReactNode } from "react";
import type { ActionDescriptor } from "@angee/ui";
import type { AuthoredMutationOptions } from "@angee/refine";
import type { DocumentType } from "@angee/gql/console";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import { KnowledgeCreateVaultFrom } from "../data/documents";

type CloneData = DocumentType<typeof KnowledgeCreateVaultFrom>;
const mocks = vi.hoisted(() => ({
  submit: null as ActionDescriptor["submit"] | null,
  options: null as AuthoredMutationOptions<CloneData> | null,
  mutation: vi.fn(),
  key: vi.fn(),
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
  Action: ({ submit }: ActionDescriptor) => { mocks.submit = submit; return null; },
  Column: () => null,
  Field: () => null,
  Form: ({ children }: { children?: ReactNode }) => <>{children}</>,
  List: ({ children }: { children?: ReactNode }) => <>{children}</>,
  DrawerResourceList: ({ children }: { children?: ReactNode }) => <>{children}</>,
  createClientKey: mocks.key,
}));

import { KnowledgeSettingsPage } from "./KnowledgeSettingsPage";

beforeEach(() => {
  mocks.submit = null;
  mocks.options = null;
  let sequence = 0;
  mocks.key.mockReset().mockImplementation(() => `clone-key-${++sequence}`);
  mocks.mutation.mockReset().mockResolvedValue({ create_vault_from: { ok: false, message: "Try again" } });
  mocks.canonicalize.mockReset().mockReturnValue(["knowledge.vault", "knowledge.page", "knowledge.markdownpage"]);
  mocks.invalidates.mockReset().mockReturnValue([]);
});

afterEach(cleanup);

async function submit(name = "Copy", template = "vlt_template") {
  if (!mocks.submit) throw new Error("Clone action was not registered");
  return mocks.submit({ name }, { record: { id: template }, selectedIds: [] });
}

test("failed responses and transport retries retain the same client key", async () => {
  render(<KnowledgeSettingsPage />);
  const rejected = await submit(" Copy ");
  expect(rejected?.ok).toBe(false);
  mocks.mutation.mockRejectedValueOnce(new Error("Offline"));
  await expect(submit()).rejects.toThrow("Offline");
  await submit();
  expect(mocks.key).toHaveBeenCalledTimes(1);
  for (const [variables] of mocks.mutation.mock.calls) {
    expect(variables).toEqual({ template: "vlt_template", name: "Copy", owned: true, client_creation_key: "clone-key-1" });
  }
});

test("success clears the retry identity so another clone gets a new key", async () => {
  mocks.mutation.mockResolvedValue({ create_vault_from: { ok: true, id: "vlt_created", message: "Created" } });
  render(<KnowledgeSettingsPage />);
  expect(await submit()).toEqual({ ok: true, id: "vlt_created", message: "Created" });
  await submit();
  expect(mocks.mutation).toHaveBeenNthCalledWith(1, expect.objectContaining({ client_creation_key: "clone-key-1" }));
  expect(mocks.mutation).toHaveBeenNthCalledWith(2, expect.objectContaining({ client_creation_key: "clone-key-2" }));
});

test("changing the name or template starts a new creation request", async () => {
  render(<KnowledgeSettingsPage />);
  await submit();
  await submit("Changed");
  await submit("Changed", "vlt_other");
  expect(mocks.key).toHaveBeenCalledTimes(3);
  expect(mocks.mutation).toHaveBeenLastCalledWith({
    template: "vlt_other", name: "Changed", owned: true, client_creation_key: "clone-key-3",
  });
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
