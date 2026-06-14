import { useState, type ReactElement } from "react";

import {
  Column,
  ControlBandProvider,
  DataPage,
  Field,
  Form,
  List,
  NEW_RECORD_ID,
} from "@angee/base";
import { useKnowledgeT } from "../i18n";

const VAULT_MODEL = "knowledge.Vault";

/**
 * The knowledge admin console: a managed list of vaults whose record form opens
 * in a drawer. Create / edit / delete are gated server-side (a vault is owned by
 * its creator; `createVault` preflights the gate). Vaults carry no immutable
 * fields, so the form is plain full CRUD.
 */
export function KnowledgeSettingsPage(): ReactElement {
  const t = useKnowledgeT();
  const [recordId, setRecordId] = useState<string | undefined>(undefined);
  return (
    <div className="mx-auto flex w-full max-w-[1100px] flex-col gap-6 px-6 py-6 sm:px-8">
      <header className="grid gap-0.5">
        <h2 className="text-15 font-semibold text-fg">
          {t("knowledge.settings.title")}
        </h2>
        <p className="text-13 text-fg-muted">
          {t("knowledge.settings.description")}
        </p>
      </header>
      {/* Drawer form: its control band must render inline (in the dialog), not
          portal into the shell's band. */}
      <ControlBandProvider host={undefined}>
        <DataPage
          model={VAULT_MODEL}
          placement="drawer"
          recordId={recordId}
          onSelect={(id) => setRecordId(id ?? NEW_RECORD_ID)}
          onClose={() => setRecordId(undefined)}
        >
          <List model={VAULT_MODEL} order={{ name: "ASC" }}>
            <Column field="name" />
            <Column field="ownerLabel" />
            <Column field="updatedAt" />
          </List>
          <Form model={VAULT_MODEL}>
            <Field name="name" widget="text" title />
            <Field name="description" widget="textarea" />
            <Field name="icon" />
            <Field name="accent" />
          </Form>
        </DataPage>
      </ControlBandProvider>
    </div>
  );
}
