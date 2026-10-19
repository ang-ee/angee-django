import { useAuthoredMutation, useAuthoredQuery } from "@angee/refine";
import {
  Button, EmptyState, ErrorBanner, PaneRow, PaneRows, RecordReference, Select, Skeleton, SkeletonStatus, errorMessage,
  useRouteHref, useRuntimeViewAs, type ChatterViewContext, type ContainerChild, type RelatedGroupContent,
} from "@angee/ui";
import { useNavigate } from "@tanstack/react-router";
import { useMemo, useState, type ReactElement } from "react";

import {
  KNOWLEDGE_LIST_LIMIT, KnowledgeBindRecord, KnowledgePage, KnowledgePageRecordBindings, KnowledgePages, KnowledgeRecordPages,
  KnowledgeUnbindRecord, PAGE_MODEL, PAGE_READ_MODELS, RECORD_BINDING_MODEL,
} from "./data/documents";
import { useKnowledgeT } from "./i18n";

const BINDING_MODELS = [RECORD_BINDING_MODEL] as const;

/** The Related group's record, as the pane expects it. */
export function recordPagesTarget(context: ChatterViewContext): RecordPagesTarget {
  return {
    modelLabel: context.route?.modelLabel ?? "",
    recordId: context.view.kind === "record" ? context.view.sqid ?? "" : "",
  };
}

function useRecordPagesRead(context: ChatterViewContext, role?: string) {
  const { modelLabel, recordId } = recordPagesTarget(context);
  const variables = useMemo(() => ({ modelLabel, recordId, role: role ?? null }), [modelLabel, recordId, role]);
  return useAuthoredQuery(KnowledgeRecordPages, variables, {
    enabled: Boolean(modelLabel && recordId), models: BINDING_MODELS,
  });
}

function useRecordPagesCount(context: ChatterViewContext, role?: string): number | undefined {
  return useRecordPagesRead(context, role).data?.record_knowledge_bindings.filter((binding) => binding.page !== null).length;
}

/** Bind is offered where the record's type declares a binding arm the actor may write. */
function useRecordPagesVerb(context: ChatterViewContext, role?: string): boolean {
  return useRecordPagesRead(context, role).data?.record_knowledge_can_bind ?? false;
}

export interface RecordPagesGroupOptions {
  label?: string;
  role?: string;
  sequence?: number;
  when?: RelatedGroupContent["when"];
}

/** A role-scoped Pages group of the Related tab, for `related#groups` or a `<model>#groups` address. */
export function recordPagesGroup(options: RecordPagesGroupOptions = {}): ContainerChild<RelatedGroupContent> {
  const { label = "Pages", role, sequence = 20, when } = options;
  return {
    sequence,
    content: {
      label,
      // A record of a type whose schema lets it carry bindings.
      when: (context) => (context.route?.recordEdges?.includes(RECORD_BINDING_MODEL) ?? false) && (when?.(context) ?? true),
      useCount: (context) => useRecordPagesCount(context, role),
      useVerb: (context) => useRecordPagesVerb(context, role),
      render: (context) => <RecordPagesPane target={recordPagesTarget(context)} role={role} />,
    },
  };
}

/** The record whose page bindings a pane lists. */
export interface RecordPagesTarget { modelLabel: string; recordId: string }

/** Page bindings as rows linking to the reader, unbind as each row's verb, and the
 *  writer-gated bind control as the group's one verb. */
export function RecordPagesPane({ target, role }: { target: RecordPagesTarget; role?: string }): ReactElement {
  const t = useKnowledgeT();
  const preview = useRuntimeViewAs();
  const routeHref = useRouteHref();
  const navigate = useNavigate();
  const { modelLabel, recordId } = target;
  const variables = useMemo(() => ({ modelLabel, recordId, role: role ?? null }), [modelLabel, recordId, role]);
  const bindingsQuery = useAuthoredQuery(KnowledgeRecordPages, variables, {
    enabled: Boolean(modelLabel && recordId), models: BINDING_MODELS,
  });
  const canBind = bindingsQuery.data?.record_knowledge_can_bind ?? false;
  const pagesQuery = useAuthoredQuery(KnowledgePages, { limit: KNOWLEDGE_LIST_LIMIT, offset: 0 }, {
    enabled: canBind, models: [PAGE_MODEL],
  });
  const [bind] = useAuthoredMutation(KnowledgeBindRecord, { invalidateModels: BINDING_MODELS });
  const [unbind] = useAuthoredMutation(KnowledgeUnbindRecord, { invalidateModels: BINDING_MODELS });
  const [pageToBind, setPageToBind] = useState("");
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
      if (remove) await unbind({ input });
      else await bind({ input });
    } catch (error) {
      setWriteError(errorMessage(error, t("record.writeError")));
    } finally {
      setBusy(false);
    }
  }

  if (bindingsQuery.isPending && !bindingsQuery.data) return <SkeletonStatus label={t("record.loading")} className="space-y-2 p-2">
    <Skeleton className="h-8" /><Skeleton className="h-8" />
  </SkeletonStatus>;
  if (bindingsQuery.error && !bindingsQuery.data) return <ErrorBanner description={bindingsQuery.error.message} />;

  return <div className="flex min-h-0 flex-col">
    {writeError ? <ErrorBanner description={writeError} /> : null}
    {pagesQuery.error && canBind ? <ErrorBanner description={pagesQuery.error.message} /> : null}
    {bindings.length ? <PaneRows>
      {bindings.map((binding) => {
        const href = routeHref("knowledge.page", { id: binding.page ?? "" });
        const unbindable = canBind && binding.page_can_write && writesEnabled;
        return <li key={binding.id} className="flex items-center gap-1">
          <PaneRow
            icon="note"
            title={binding.page_title || binding.page}
            sub={role ? undefined : binding.role}
            href={href}
            onOpen={() => void navigate({ href } as never)}
          />
          {unbindable ? <Button type="button" size="sm" variant="ghost" className="shrink-0"
            disabled={busy} aria-label={`${t("record.unbind")} ${binding.page_title || binding.page}${role ? "" : ` (${binding.role})`}`}
            onClick={() => binding.page && void changeBinding(binding.page, true, binding.role)}>{t("record.unbind")}</Button> : null}
        </li>;
      })}
    </PaneRows> : <EmptyState icon="knowledge" title={t("record.empty")} className="min-h-24 p-3" />}
    {canBind && writesEnabled ? <div className="flex items-center gap-2 px-1 py-1">
      {pagesQuery.isPending && !pagesQuery.data ? <SkeletonStatus label={t("record.loadingPages")} className="flex-1"><Skeleton className="h-7" /></SkeletonStatus> :
        <Select aria-label={t("record.choosePage")} className="min-w-0 flex-1" value={chosenPage}
          options={unboundPages.map((page) => ({ value: page.id, label: page.title }))}
          placeholder={t("record.choosePage")} onValueChange={setPageToBind} />}
      <Button type="button" size="sm" variant="ghost" disabled={!chosenPage || busy}
        onClick={() => void changeBinding(chosenPage, false)}>{t("record.bind")}</Button>
    </div> : null}
  </div>;
}

/** The pages that link to the open page: a `knowledge.Page#groups` child. */
export const pageBacklinksGroup: ContainerChild<RelatedGroupContent> = {
  sequence: 10,
  content: {
    label: "Backlinks",
    useCount: (context) => usePageDetail(context).data?.pages_by_pk?.backlinks.length,
    render: (context) => <PageBacklinks context={context} />,
  },
};

/** The records the open page is bound to: a `knowledge.Page#groups` child. */
export const pageBoundToGroup: ContainerChild<RelatedGroupContent> = {
  sequence: 20,
  content: {
    label: "Bound to",
    useCount: (context) => usePageRecordBindings(context).data?.page_record_bindings.length,
    render: (context) => <PageBoundTo context={context} />,
  },
};

function pageId(context: ChatterViewContext): string {
  return context.view.kind === "record" ? context.view.sqid ?? "" : "";
}

function usePageDetail(context: ChatterViewContext) {
  const id = pageId(context);
  return useAuthoredQuery(KnowledgePage, { id }, { enabled: Boolean(id), models: PAGE_READ_MODELS });
}

function usePageRecordBindings(context: ChatterViewContext) {
  const page = pageId(context);
  return useAuthoredQuery(KnowledgePageRecordBindings, { page }, { enabled: Boolean(page), models: BINDING_MODELS });
}

function PageBacklinks({ context }: { context: ChatterViewContext }): ReactElement {
  const t = useKnowledgeT();
  const routeHref = useRouteHref();
  const navigate = useNavigate();
  const query = usePageDetail(context);
  const backlinks = query.data?.pages_by_pk?.backlinks ?? [];
  if (query.error) return <ErrorBanner description={query.error.message} />;
  if (!backlinks.length) return <EmptyState icon="link" title={t("backlinks.emptyTitle")} description={t("backlinks.emptyDescription")} className="min-h-24 p-3" />;
  return <PaneRows>
    {backlinks.map((backlink) => {
      const href = routeHref("knowledge.page", { id: backlink.page });
      return <li key={`${backlink.page}:${backlink.display_text}`}>
        <PaneRow icon="note" title={backlink.title} sub={backlink.display_text || undefined} href={href} onOpen={() => void navigate({ href } as never)} />
      </li>;
    })}
  </PaneRows>;
}

function PageBoundTo({ context }: { context: ChatterViewContext }): ReactElement {
  const t = useKnowledgeT();
  const query = usePageRecordBindings(context);
  const bindings = query.data?.page_record_bindings ?? [];
  if (query.error) return <ErrorBanner description={query.error.message} />;
  if (!bindings.length) return <EmptyState icon="link" title={t("boundTo.empty")} className="min-h-24 p-3" />;
  return <PaneRows>
    {bindings.map((binding) => <li key={binding.id} className="flex items-baseline gap-2 px-1 py-1.5 text-13">
      <span className="min-w-0 flex-1 truncate"><RecordReference model={binding.model_label} id={binding.record_id} /></span>
      <span className="shrink-0 text-12 text-fg-muted">{binding.role}</span>
    </li>)}
  </PaneRows>;
}
