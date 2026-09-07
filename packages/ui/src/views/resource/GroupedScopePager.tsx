import * as React from "react";
import { MAX_PAGE_SIZE } from "@angee/refine";
import type { UiTranslate } from "../../i18n";
import { cn } from "../../lib/cn";
import { Pager } from "../../ui/pager";
import { textRoleVariants } from "../../ui/text";
import { PAGE_SIZE_OPTIONS } from "./page-size";
import type { GroupedListPager } from "./resource-view-list-body";

function formatPagerNumber(value: number): string {
  return value.toLocaleString();
}

/** Native group/record pagination shared by the list and board renderers. */
export function GroupedScopePager({
  pager,
  label,
  onPageChange,
  onPageSizeChange,
  t,
}: {
  pager: GroupedListPager;
  label: string;
  onPageChange: (key: string, page: number) => void;
  onPageSizeChange: (key: string, pageSize: number) => void;
  t: UiTranslate;
}): React.ReactElement {
  const { pageKey, page, pageSize, total, unit, pending } = pager;
  const navLabel = t(
    unit === "groups" ? "list.pagerSubject.groups" : "list.pagerSubject.records",
    { label },
  );
  return (
    <nav
      aria-label={navLabel}
      aria-busy={pending}
      onClick={(event) => event.stopPropagation()}
      className={cn(textRoleVariants({ role: "meta" }), "flex shrink-0 items-center justify-end gap-2 whitespace-nowrap font-normal")}
    >
      <Pager
        page={page}
        pageSize={pageSize}
        total={total}
        hasPrev={!pending && page > 1}
        hasNext={!pending && total !== undefined && page * pageSize < total}
        onPageChange={(next) => onPageChange(pageKey, next)}
        unit={unit === "groups" ? "groups" : undefined}
        subject={navLabel}
        disabled={pending}
        pageSizeOptions={PAGE_SIZE_OPTIONS}
        maxPageSize={MAX_PAGE_SIZE}
        onPageSizeChange={(size) => onPageSizeChange(pageKey, size)}
        previousLabel={t("pager.previousSubject", { subject: navLabel })}
        nextLabel={t("pager.nextSubject", { subject: navLabel })}
        formatNumber={formatPagerNumber}
      />
    </nav>
  );
}
