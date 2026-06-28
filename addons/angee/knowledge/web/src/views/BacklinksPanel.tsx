import { type ReactElement } from "react";

import { cn, EmptyState, Glyph, SectionEyebrow, textRoleVariants } from "@angee/ui";

import { useKnowledgeT } from "../i18n";
import type { Backlink } from "../data/documents";

export interface BacklinksPanelProps {
  backlinks: readonly Backlink[];
  /** Open a linking page by its node id. */
  onOpen: (pageId: string) => void;
}

/**
 * The Obsidian-style backlinks rail: every readable page that links to the open
 * page, each a button that jumps to the source. Derived server-side from the
 * vault's wikilinks.
 */
export function BacklinksPanel({
  backlinks,
  onOpen,
}: BacklinksPanelProps): ReactElement {
  const t = useKnowledgeT();
  if (backlinks.length === 0) {
    return (
      <EmptyState
        fill
        icon="link"
        title={t("knowledge.backlinks.emptyTitle")}
        description={t("knowledge.backlinks.emptyDescription")}
      />
    );
  }

  return (
    <div className="flex h-full flex-col gap-1 overflow-auto p-3">
      <SectionEyebrow as="h3" className="px-1 pb-1">
        {t("knowledge.backlinks.heading")} · {backlinks.length}
      </SectionEyebrow>
      {backlinks.map((backlink) => (
        <button
          key={`${backlink.page}:${backlink.display_text}`}
          type="button"
          className="flex min-w-0 flex-col gap-0.5 rounded-6 px-2 py-1.5 text-left outline-none transition-colors hover:bg-inset focus-visible:focus-ring"
          onClick={() => onOpen(backlink.page)}
        >
          <span className="flex min-w-0 items-center gap-2 text-13 font-medium text-fg">
            <Glyph decorative name="note" className="text-fg-muted" />
            <span className="truncate">{backlink.title}</span>
          </span>
          {backlink.display_text ? (
            <span className={cn(textRoleVariants({ role: "caption", truncate: true }), "pl-6")}>
              {backlink.display_text}
            </span>
          ) : null}
        </button>
      ))}
    </div>
  );
}
