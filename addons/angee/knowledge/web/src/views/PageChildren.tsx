import { useModelMetadata } from "@angee/metadata";
import { ListView, PaneRow, ResourceViewProvider, formatDate, useRouteHref } from "@angee/ui";
import { useMemo } from "react";
import { PAGE_MODEL } from "../data/documents";
import { pageSource } from "../data/page-source";
import { useKnowledgeT } from "../i18n";

/** Child pages use the native paged collection and record links. */
export function PageChildren({ page, vault }: { page: string; vault: string }) {
  const t = useKnowledgeT();
  const href = useRouteHref();
  const metadata = useModelMetadata(PAGE_MODEL);
  const source = useMemo(() => metadata ? pageSource(metadata, vault, { parent: { exact: page } }) : null,
    [metadata, page, vault]);
  if (!source) return null;
  return <ResourceViewProvider key={page} scope="local" resource={PAGE_MODEL} initialState={{ pageSize: 20 }}>
    <ListView resource={PAGE_MODEL} source={source} presentation="embedded" selectable={false}
      columns={[{ field: "title", header: t("editor.titleLabel") }]}
      availableViews={["list"]}
      renderPaneRow={(child) => <PaneRow icon={child.icon ?? undefined} title={child.title}
        href={href("knowledge.page", { id: child.id })} meta={formatDate(child.updated_at)}
        sub={child.created_by_label ?? undefined} />}
      emptyContent={{ title: t("folder.emptyTitle"), description: t("folder.emptyDescription") }} />
  </ResourceViewProvider>;
}
