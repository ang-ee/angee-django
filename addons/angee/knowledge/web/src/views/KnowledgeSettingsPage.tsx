import { useCallback, useRef, type ReactElement } from "react";

import { useCanonicalResourceModelLabels, useResourceInvalidates } from "@angee/metadata";
import { extractActionOutcome, useAuthoredMutation } from "@angee/refine";

import {
  Action, Column, createClientKey, DrawerResourceList, Field, Form, List, SettingsSection, SettingsShell,
  type ActionDescriptor } from "@angee/ui";
import { KnowledgeCreateVaultFrom, PAGE_READ_MODELS } from "../data/documents";
import { useKnowledgeT } from "../i18n";

const VAULT_MODEL = "knowledge.Vault";
const CLONE_MODELS = [VAULT_MODEL, ...PAGE_READ_MODELS];

/**
 * The knowledge admin console: a managed list of vaults whose record form opens
 * in a drawer. Create / edit / delete are gated server-side; ownership and replay
 * receipts are managed by their dedicated server contracts.
 */
export function KnowledgeSettingsPage(): ReactElement {
  const t = useKnowledgeT();
  const pendingClone = useRef<{ template: string; name: string; key: string } | null>(null);
  const invalidateModels = useCanonicalResourceModelLabels(CLONE_MODELS);
  const invalidates = useResourceInvalidates(invalidateModels);
  const [createFrom] = useAuthoredMutation(KnowledgeCreateVaultFrom, {
    invalidateModels,
    invalidates,
    shouldInvalidate: (data) => data?.create_vault_from.ok === true,
  });
  const cloneSubmit = useCallback<NonNullable<ActionDescriptor["submit"]>>(
    async (values, context) => {
      const template = context.record?.id;
      if (typeof template !== "string" || !template || typeof values.name !== "string") {
        return { ok: false, message: t("vault.cloneFailed") };
      }
      const name = values.name.trim();
      if (pendingClone.current?.template !== template || pendingClone.current.name !== name) {
        pendingClone.current = { template, name, key: createClientKey() };
      }
      const data = await createFrom({
        template, name, owned: true, client_creation_key: pendingClone.current.key,
      });
      const outcome = extractActionOutcome(data, "create_vault_from");
      if (outcome?.ok) pendingClone.current = null;
      return outcome;
    },
    [createFrom, t],
  );
  return (
    <SettingsShell maxWidth="1100" gap="6">
      <SettingsSection
        title={t("settings.title")}
        description={t("settings.description")}
      />
      <DrawerResourceList resource={VAULT_MODEL}>
        <List resource={VAULT_MODEL} order={{ name: "ASC" }}>
          <Column field="name" />
          <Column field="owner_label" />
          <Column field="updated_at" />
        </List>
        <Form resource={VAULT_MODEL}>
          <Field name="name" widget="text" title />
          <Field name="description" widget="textarea" />
          <Field name="icon" />
          <Field name="accent" />
          <Action
            id="create-from-template"
            label={t("vault.clone")}
            icon="copy"
            args={[{ name: "name", label: t("vault.cloneName"), widget: "text" }]}
            submit={cloneSubmit}
            // Keep the action off the create drawer until a record is loaded.
            visibleWhen={() => true}
          />
        </Form>
      </DrawerResourceList>
    </SettingsShell>
  );
}
