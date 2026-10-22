import type { ReactElement } from "react";

import { useUiT } from "../../i18n";
import { Pager } from "../../ui/pager";

export interface RecordNavigation {
  /** Undefined when the open record isn't in the loaded slice. */
  current?: number;
  total?: number;
  onPrev?: () => void;
  onNext?: () => void;
}

/** The record page's pager: the `Pager` in record mode ("n / N") over the navigator's order.
 * @kind fragment
 * @owner Pager
 */
export function RecordPager({
  navigation,
}: {
  navigation: RecordNavigation;
}): ReactElement {
  const t = useUiT();
  return (
    <nav aria-label={t("recordPager.navigation")} className="flex items-center gap-1">
      <Pager
        mode="record"
        page={navigation.current ?? 0}
        pageSize={1}
        total={navigation.total}
        hasPrev={Boolean(navigation.onPrev)}
        hasNext={Boolean(navigation.onNext)}
        onPageChange={(next) => (next < (navigation.current ?? 0) ? navigation.onPrev : navigation.onNext)?.()}
      />
    </nav>
  );
}
