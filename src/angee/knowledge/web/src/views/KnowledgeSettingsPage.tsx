import { useCallback, type ReactElement } from "react";

import { AuthoredEnvelopeError, extractActionOutcome, failedWriteOutcome } from "@angee/refine";

import {
  Action, Column, DrawerResourceList, Field, Form, List, SettingsSection, SettingsShell,
  useAuthoredResourceMutation, type ActionDescriptor, type ActionFormContext } from "@angee/ui";
import { KnowledgeCreateVaultFrom, PAGE_READ_MODELS } from "../data/documents";
import { useKnowledgeT } from "../i18n";

const VAULT_MODEL = "knowledge.Vault";
const CLONE_MODELS = [VAULT_MODEL, ...PAGE_READ_MODELS];
/** The clone dialog of one template is one draft: its key lasts from the first attempt until a vault is created. */
const cloneScope = (context: ActionFormContext) => `knowledge.vault-from-template:${String(context.record?.id ?? "")}`;

/**
 * The knowledge admin console: a managed list of vaults whose record form opens
 * in a drawer. Create / edit / delete are gated server-side; ownership and replay
 * receipts are managed by their dedicated server contracts.
 */
export function KnowledgeSettingsPage(): ReactElement {
  const t = useKnowledgeT();
  const [createFrom] = useAuthoredResourceMutation(KnowledgeCreateVaultFrom, {
    invalidateModels: CLONE_MODELS,
    shouldInvalidate: (data) => data?.create_vault_from.ok === true,
  });
  const cloneSubmit = useCallback<NonNullable<ActionDescriptor["submit"]>>(
    async (values, context, options) => {
      const template = context.record?.id;
      if (typeof template !== "string" || !template || typeof values.name !== "string") {
        return { ok: false, message: t("vault.cloneFailed") };
      }
      const name = values.name.trim();
      try {
        // The action dialog holds the draft's key (`idempotencyScope`): every attempt until a vault is created carries it.
        const data = await createFrom({ template, name, owned: true, idempotency_key: context.idempotencyKey }, options);
        return extractActionOutcome(data, "create_vault_from");
      } catch (cause) {
        if (failedWriteOutcome(cause) !== "conflict") throw cause;
        // An earlier attempt created a vault under this draft's key. Still a conflict, so the dialog's
        // draft ends and the next attempt is a new one, in this action's words.
        throw new AuthoredEnvelopeError(t("vault.clonedEarlier"), "CREATION_KEY_CONFLICT");
      }
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
            placement="menu"
            label={t("vault.clone")}
            icon="copy"
            args={[{ name: "name", label: t("vault.cloneName"), widget: "text" }]}
            submit={cloneSubmit}
            idempotencyScope={cloneScope}
            // Keep the action off the create drawer until a record is loaded.
            visibleWhen={() => true}
          />
        </Form>
      </DrawerResourceList>
    </SettingsShell>
  );
}
