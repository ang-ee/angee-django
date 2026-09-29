import { useAuthoredQuery } from "@angee/refine";
import { ErrorBanner, Skeleton, SkeletonStatus } from "@angee/ui";
import { useCallback, useRef, useState, type ReactElement } from "react";

import { KnowledgePage, PAGE_READ_MODELS } from "./data/documents";
import { useKnowledgeT } from "./i18n";
import { PageEditor } from "./views/PageEditor";
import { PageReader } from "./views/PageReader";

/** `[[wikilinks]]` resolve only under a wikilink resolver provider. */
export interface KnowledgePageViewProps {
  pageId: string;
  /** An enclosing page may supply its existing delete action. */
  onDelete?: () => void;
}

/** Embed one actor-readable page and its edit transition, without shell chrome. */
export function KnowledgePageView(props: KnowledgePageViewProps): ReactElement {
  return <KnowledgePageContent key={props.pageId} {...props} />;
}

function KnowledgePageContent({
  pageId,
  onDelete,
}: KnowledgePageViewProps): ReactElement | null {
  const t = useKnowledgeT();
  const [editing, setEditing] = useState(false);
  const restoreEditFocus = useRef(false);
  const editButtonRef = useCallback((button: HTMLButtonElement | null) => {
    if (button && restoreEditFocus.current) {
      button.focus();
      restoreEditFocus.current = false;
    }
  }, []);
  const query = useAuthoredQuery(KnowledgePage, { id: pageId }, {
    models: PAGE_READ_MODELS,
  });
  const detail = query.data?.pages_by_pk;
  const canWrite = detail?.permissions.includes("write") ?? false;
  if (editing && detail && !canWrite) {
    restoreEditFocus.current = false;
    setEditing(false);
  }
  if (!detail) {
    if (query.isPending) return <SkeletonStatus label={t("page.loading")} className="space-y-3 p-4">
      <Skeleton className="h-7 w-2/3" /><Skeleton className="h-4" /><Skeleton className="h-4 w-5/6" />
    </SkeletonStatus>;
    if (query.error) return <ErrorBanner description={query.error.message} />;
    return null;
  }

  return editing && canWrite ? (
    <PageEditor
      detail={detail}
      onDelete={onDelete}
      onDone={() => {
        restoreEditFocus.current = true;
        setEditing(false);
      }}
    />
  ) : (
    <PageReader
      detail={detail}
      onEdit={canWrite ? () => setEditing(true) : undefined}
      editButtonRef={editButtonRef}
      onDelete={onDelete}
    />
  );
}
