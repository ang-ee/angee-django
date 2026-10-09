import { useAuthoredMutation, useAuthoredQuery } from "@angee/refine";
import { Button, EmptyState, ErrorBanner, Select, Skeleton, SkeletonStatus, errorMessage, useRuntimeViewAs, type ChatterTabContent, type ChatterViewContext, type ContainerChild } from "@angee/ui";
import { useMemo, useState, type ReactElement } from "react";

import { KNOWLEDGE_LIST_LIMIT, KnowledgeBindRecord, KnowledgePages, KnowledgeRecordPages, KnowledgeUnbindRecord, RECORD_BINDING_MODEL } from "./data/documents";
import { useKnowledgeT } from "./i18n";
import { KnowledgePageView } from "./KnowledgePageView";

const BINDING_MODELS = [RECORD_BINDING_MODEL] as const;

/** The chatter tab's record, as the pane expects it. */
export function recordPagesTarget(context: ChatterViewContext): RecordPagesTarget {
  return {
    modelLabel: context.route?.modelLabel ?? "",
    recordId: context.view.kind === "record" ? context.view.sqid ?? "" : "",
  };
}

function useRecordPagesCount(context: ChatterViewContext, role?: string): number | undefined {
  const { modelLabel, recordId } = recordPagesTarget(context);
  const variables = useMemo(() => ({ modelLabel, recordId, role: role ?? null }), [modelLabel, recordId, role]);
  const query = useAuthoredQuery(KnowledgeRecordPages, variables, {
    enabled: Boolean(modelLabel && recordId), models: BINDING_MODELS,
  });
  return query.data?.record_knowledge_bindings.filter((binding) => binding.page !== null).length;
}

export interface RecordPagesTabOptions {
  label?: string;
  role?: string;
  sequence?: number;
  when?: ChatterTabContent["when"];
  /** Earlier ids a `?chatterTab=` link may still carry. */
  aliases?: readonly string[];
}

/** A role-scoped Pages tab, for an addon's `<model>#aside` or `record#aside`. */
export function recordPagesTab(options: RecordPagesTabOptions = {}): ContainerChild<ChatterTabContent> {
  const { label = "Pages", role, sequence = 40, when, aliases } = options;
  return {
    sequence,
    content: {
      label, icon: "knowledge",
      ...(aliases ? { aliases } : {}),
      // A record of a type whose schema lets it carry bindings; a `<model>#aside` placement also reaches list views.
      when: (context) => context.view.kind === "record"
        && (context.route?.recordEdges?.includes(RECORD_BINDING_MODEL) ?? false)
        && (when?.(context) ?? true),
      useCount: (context) => useRecordPagesCount(context, role),
      render: (context) => <RecordPagesPane target={recordPagesTarget(context)} role={role} />,
    },
  };
}

/** The record whose page bindings a pane lists. */
export interface RecordPagesTarget { modelLabel: string; recordId: string }

/** Page bindings, role filter, inline reader, and writer-gated bind controls; the
 *  chatter tab and a page section both hand it the record they show. */
export function RecordPagesPane({ target, role }: { target: RecordPagesTarget; role?: string }): ReactElement {
  const t = useKnowledgeT();
  const preview = useRuntimeViewAs();
  const { modelLabel, recordId } = target;
  const variables = useMemo(() => ({ modelLabel, recordId, role: role ?? null }), [modelLabel, recordId, role]);
  const bindingsQuery = useAuthoredQuery(KnowledgeRecordPages, variables, {
    enabled: Boolean(modelLabel && recordId), models: BINDING_MODELS,
  });
  const canBind = bindingsQuery.data?.record_knowledge_can_bind ?? false;
  const pagesQuery = useAuthoredQuery(KnowledgePages, { limit: KNOWLEDGE_LIST_LIMIT, offset: 0 }, {
    enabled: canBind, models: ["knowledge.Page"],
  });
  const [bind] = useAuthoredMutation(KnowledgeBindRecord, { invalidateModels: BINDING_MODELS });
  const [unbind] = useAuthoredMutation(KnowledgeUnbindRecord, { invalidateModels: BINDING_MODELS });
  const [pageToBind, setPageToBind] = useState("");
  const [selectedPage, setSelectedPage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [writeError, setWriteError] = useState<string | null>(null);
  const bindings = bindingsQuery.data?.record_knowledge_bindings.filter((binding) => binding.page !== null) ?? [];
  const writablePages = pagesQuery.data?.pages?.filter((page) => page.permissions.includes("write")) ?? [];
  const unboundPages = writablePages.filter((page) => !bindings.some((binding) => binding.page === page.id && binding.role === (role ?? "related")));
  const chosenPage = unboundPages.some((page) => page.id === pageToBind) ? pageToBind : unboundPages[0]?.id ?? "";
  const writesEnabled = !preview.viewAs && !preview.pending;

  async function changeBinding(page: string, remove: boolean, bindingRole = role ?? "related"): Promise<void> {
    setBusy(true);
    setWriteError(null);
    try {
      const input = { model_label: modelLabel, record_id: recordId, page, role: bindingRole };
      if (remove) {
        await unbind({ input });
        if (selectedPage === page) setSelectedPage(null);
      } else {
        await bind({ input });
        setSelectedPage(page);
      }
    } catch (error) {
      setWriteError(errorMessage(error, t("record.writeError")));
    } finally {
      setBusy(false);
    }
  }

  if (bindingsQuery.isPending && !bindingsQuery.data) return <SkeletonStatus label={t("record.loading")} className="space-y-3 p-4">
    <Skeleton className="h-9" /><Skeleton className="h-12" /><Skeleton className="h-12" />
  </SkeletonStatus>;
  if (bindingsQuery.error && !bindingsQuery.data) return <ErrorBanner description={bindingsQuery.error.message} />;

  return <div className="flex h-full min-h-0 flex-col">
    {writeError ? <ErrorBanner description={writeError} /> : null}
    {pagesQuery.error && canBind ? <ErrorBanner description={pagesQuery.error.message} /> : null}
    {canBind && writesEnabled ? <div className="flex gap-2 border-b border-border-subtle p-3">
      {pagesQuery.isPending && !pagesQuery.data ? <SkeletonStatus label={t("record.loadingPages")} className="flex-1"><Skeleton className="h-9" /></SkeletonStatus> :
        <Select aria-label={t("record.choosePage")} className="min-w-0 flex-1" value={chosenPage}
          options={unboundPages.map((page) => ({ value: page.id, label: page.title }))}
          placeholder={t("record.choosePage")} onValueChange={setPageToBind} />}
      <Button type="button" size="sm" variant="secondary" disabled={!chosenPage || busy}
        onClick={() => void changeBinding(chosenPage, false)}>{t("record.bind")}</Button>
    </div> : null}
    {bindings.length ? <ul className="min-h-0 overflow-auto border-b border-border-subtle">
      {bindings.map((binding) => <li key={binding.id} className="flex items-center gap-2 px-3 py-2">
        <button type="button" className="min-w-0 flex-1 truncate text-left text-sm hover:underline"
          aria-pressed={selectedPage === binding.page} onClick={() => setSelectedPage(binding.page)}>
          {binding.page_title || binding.page}
        </button>
        {!role ? <span className="text-xs text-fg-muted">{binding.role}</span> : null}
        {canBind && binding.page_can_write && writesEnabled ? <Button type="button" size="sm" variant="ghost"
          disabled={busy} aria-label={`${t("record.unbind")} ${binding.page_title || binding.page}${role ? "" : ` (${binding.role})`}`}
          onClick={() => binding.page && void changeBinding(binding.page, true, binding.role)}>{t("record.unbind")}</Button> : null}
      </li>)}
    </ul> : <EmptyState icon="knowledge" title={t("record.empty")} />}
    {selectedPage ? <div className="min-h-48 flex-1 overflow-auto"><KnowledgePageView pageId={selectedPage} /></div> : null}
  </div>;
}
