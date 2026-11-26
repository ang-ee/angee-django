// @vitest-environment happy-dom

import { render } from "@testing-library/react";
import type { ReactNode } from "react";
import { AuthoredEnvelopeError, boundedGraphQLTransportError, failedWriteOutcome } from "@angee/refine";
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

import { AppRuntimeProvider } from "@angee/ui";
import { KnowledgeSettingsPage } from "./KnowledgeSettingsPage";

/** Under an app runtime, as a page is: it holds the clone draft's idempotency key. */
const AppRuntime = ({ children }: { children?: ReactNode }) => <AppRuntimeProvider runtime={{}}>{children}</AppRuntimeProvider>;

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

test("the clone dialog of one template is one draft: it declares its scope, and each attempt sends the dialog's key", async () => {
  render(<KnowledgeSettingsPage />, { wrapper: AppRuntime });
  expect(mocks.action).toMatchObject({ id: "create-from-template", placement: "menu" });
  expect(mocks.action?.idempotencyScope?.({ record: { id: "vlt_template" }, selectedIds: [] })).toBe("knowledge.vault-from-template:vlt_template");
  await mocks.submit!({ name: " Copy " }, { record: { id: "vlt_template" }, selectedIds: [], idempotencyKey: "draft-key" }, { errorNotification: false });
  expect(mocks.mutation).toHaveBeenCalledExactlyOnceWith(
    { template: "vlt_template", name: "Copy", owned: true, idempotency_key: "draft-key" }, { errorNotification: false });
});

test("an earlier attempt that already created the vault says so, and stays the conflict the dialog's draft ends on", async () => {
  mocks.mutation.mockRejectedValueOnce(boundedGraphQLTransportError({ request: {}, response: { status: 200, errors: [{
    message: "Already created.", extensions: { code: "CREATION_KEY_CONFLICT" } }] } }));
  render(<KnowledgeSettingsPage />, { wrapper: AppRuntime });
  const refusal = await submit("Copy").catch((error: unknown) => error);
  expect(refusal).toBeInstanceOf(AuthoredEnvelopeError);
  expect(refusal).toMatchObject({ message: "An earlier attempt already created a vault from this template.", code: "CREATION_KEY_CONFLICT" });
  expect(failedWriteOutcome(refusal)).toBe("conflict");
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
