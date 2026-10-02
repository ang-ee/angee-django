import { holdsPermission } from "@angee/metadata";
import { RecordThreadStream, type StreamChildItem, type StreamCreateAction } from "@angee/messaging";
import { useAuthoredMutation, useAuthoredQuery } from "@angee/refine";
import { ErrorBanner, Skeleton, SkeletonStatus, type ChatterContribution } from "@angee/ui";
import { useMemo, type ReactElement } from "react";

import { recordPagesTarget, type RecordPagesTarget } from "./RecordPagesPane";
import {
  KnowledgeBindRecord, KnowledgeRecordNotes, KnowledgeUpdatePageBody, KnowledgeVault, KnowledgeVaultByName,
  PAGE_MODEL, PAGE_READ_MODELS, RECORD_BINDING_MODEL,
} from "./data/documents";
import { usePageActions } from "./data/use-page-actions";
import { useKnowledgeT } from "./i18n";

const BINDING_MODELS = [RECORD_BINDING_MODEL, ...PAGE_READ_MODELS] as const;

export interface RecordNotesStreamProps {
  target: RecordPagesTarget;
  role: string;
  /** The vault new notes go to: its public id, or its declared name when the id differs per stack. */
  vault: string | { name: string };
  heading?: { label: string; hint?: string; audience?: string };
  composer?: { prompt?: string; submitLabel?: string; audience?: string };
}

const TITLE_LENGTH = 100;

/** The note's first line, at most `TITLE_LENGTH` characters; the page title's own text otherwise. */
function noteTitle(body: string | null | undefined, fallback = ""): string {
  const first = (body ?? "").split(/\r?\n/, 1)[0]?.trim() ?? "";
  return first ? Array.from(first).slice(0, TITLE_LENGTH).join("") : fallback.replace(/ · \d{4}-\d{2}-\d{2}T[^ ]*$/, "");
}

/** What the note says beyond its first line. */
function noteBody(body: string | null | undefined): string | null {
  const rest = (body ?? "").split(/\r?\n/).slice(1).join("\n").trim();
  return rest || null;
}

/** Role-scoped pages shown as dated notes with the shared stream composer. */
export function RecordNotesStream({ target, role, vault, heading, composer }: RecordNotesStreamProps): ReactElement {
  const t = useKnowledgeT();
  const variables = useMemo(() => ({ modelLabel: target.modelLabel, recordId: target.recordId, role }),
    [target.modelLabel, target.recordId, role]);
  const bindings = useAuthoredQuery(KnowledgeRecordNotes, variables, {
    enabled: Boolean(target.modelLabel && target.recordId), models: BINDING_MODELS,
  });
  const canBind = bindings.data?.record_knowledge_can_bind ?? false;
  const vaultById = useAuthoredQuery(KnowledgeVault, { id: typeof vault === "string" ? vault : "" }, {
    enabled: Boolean(canBind && typeof vault === "string" && vault), models: ["knowledge.Vault"],
  });
  const vaultByName = useAuthoredQuery(KnowledgeVaultByName, { name: typeof vault === "string" ? "" : vault.name }, {
    enabled: Boolean(canBind && typeof vault !== "string" && vault.name), models: ["knowledge.Vault"],
  });
  const vaultQuery = typeof vault === "string" ? vaultById : vaultByName;
  const writableVault = typeof vault === "string" ? vaultById.data?.vaults_by_pk : vaultByName.data?.vaults[0];
  const { createPage } = usePageActions();
  const [updateBody] = useAuthoredMutation(KnowledgeUpdatePageBody, {
    invalidateModels: PAGE_READ_MODELS, errorFrom: (data) => data?.update_page_body,
  });
  const [bind] = useAuthoredMutation(KnowledgeBindRecord, { invalidateModels: [RECORD_BINDING_MODEL] });

  if (bindings.isPending && !bindings.data) return <SkeletonStatus label={t("notes.loading")} className="space-y-3 p-4">
    <Skeleton className="h-9" /><Skeleton className="h-16" /><Skeleton className="h-16" />
  </SkeletonStatus>;
  if (bindings.error && !bindings.data) return <ErrorBanner description={bindings.error.message} />;

  const items: StreamChildItem[] = (bindings.data?.record_knowledge_bindings ?? [])
    .flatMap((binding): StreamChildItem[] => binding.page_detail ? [{
      id: binding.page_detail.id,
      title: noteTitle(binding.page_detail.markdown?.body, binding.page_detail.title),
      body: noteBody(binding.page_detail.markdown?.body),
      authorLabel: binding.page_detail.created_by_label,
      isSelf: false,
      audienceLabel: heading?.audience ?? composer?.audience,
      createdAt: binding.page_detail.created_at,
      thread: false,
    }] : [])
    .sort((left, right) => right.createdAt.localeCompare(left.createdAt) || left.id.localeCompare(right.id));

  const canCompose = canBind && writableVault && holdsPermission(writableVault, "write");
  const createAction: StreamCreateAction | undefined = canCompose ? {
    id: "knowledge.add-note",
    label: composer?.submitLabel ?? t("notes.add"),
    record: writableVault,
    permission: "write",
    args: [{ name: "body" }],
    submit: async (values) => {
      const body = String(values.body ?? "").trim();
      if (!body) throw new Error(t("notes.bodyRequired"));
      // Page titles are unique per vault; the stored title carries the moment, the row shows the first line.
      const title = `${noteTitle(body)} · ${new Date().toISOString()}`;
      if (!writableVault) throw new Error(t("notes.createError"));
      const page = await createPage({ vault: writableVault.id, title, kind: "note", parent: null });
      if (!page) throw new Error(t("notes.createError"));
      await updateBody({ page, body });
      await bind({ input: {
        model_label: target.modelLabel, record_id: target.recordId, page, role,
      } });
      return { ok: true, message: t("notes.added") };
    },
  } : undefined;

  return <>
    {vaultQuery.error && canBind ? <ErrorBanner description={vaultQuery.error.message} /> : null}
    <RecordThreadStream heading={{
      label: heading?.label ?? t("notes.heading"),
      hint: items.length ? heading?.hint : undefined,
      audience: heading?.audience,
    }} source={{
      kind: "children", modelLabel: PAGE_MODEL, items,
      empty: { title: t("notes.empty"), description: heading?.hint },
      createAction,
      createComposer: { bodyArg: "body", prompt: composer?.prompt ?? t("notes.prompt"),
        audience: composer?.audience },
      onCreated: () => { void bindings.refetch(); },
    }} />
  </>;
}

export interface RecordNotesContributionOptions extends Omit<RecordNotesStreamProps, "target"> {
  id: string;
  label: string;
  sequence?: number;
  when?: ChatterContribution["when"];
}

/** Declare a record's role-scoped notes in the existing chatter contract. */
export function recordNotesContribution(options: RecordNotesContributionOptions): ChatterContribution {
  const { id, label, role, vault, heading, composer, sequence = 40, when } = options;
  return {
    id, label, sequence, icon: "notes",
    when: (context) => context.view.kind === "record"
      && Boolean(context.route?.modelLabel && context.view.sqid)
      && (when?.(context) ?? true),
    render: (context) => <RecordNotesStream target={recordPagesTarget(context)} role={role}
      vault={vault} heading={heading ?? { label }} composer={composer} />,
  };
}
