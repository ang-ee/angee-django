import type { ReactElement } from "react";

import { Glyph } from "../chrome/Glyph";
import { useUiT } from "../i18n";
import { cn } from "../lib/cn";
import { Button } from "../ui/button";
import { textRoleVariants } from "../ui/text";

export interface RecordNavigation {
  /** Undefined when the open record isn't in the loaded slice. */
  current?: number;
  total: number;
  onPrev?: () => void;
  onNext?: () => void;
}

/** Shared current-record pager rendered in record detail toolbars. */
export function RecordPager({
  navigation,
}: {
  navigation: RecordNavigation;
}): ReactElement {
  const t = useUiT();
  return (
    <nav
      aria-label={t("recordPager.navigation")}
      className={cn(textRoleVariants({ role: "meta" }), "flex items-center gap-2")}
    >
      <span className="whitespace-nowrap tabular-nums">
        {navigation.current !== undefined ? (
          <>
            <span className="font-medium text-fg">
              {navigation.current.toLocaleString()}
            </span>{" "}
            / {navigation.total.toLocaleString()}
          </>
        ) : (
          <>/ {navigation.total.toLocaleString()}</>
        )}
      </span>
      <div className="flex items-center gap-1">
        <Button
          type="button"
          variant="ghost"
          size="iconSm"
          aria-label={t("recordPager.prev")}
          disabled={!navigation.onPrev}
          onClick={navigation.onPrev}
        >
          <Glyph name="chevron-left" className="glyph" />
        </Button>
        <Button
          type="button"
          variant="ghost"
          size="iconSm"
          aria-label={t("recordPager.next")}
          disabled={!navigation.onNext}
          onClick={navigation.onNext}
        >
          <Glyph name="chevron-right" className="glyph" />
        </Button>
      </div>
    </nav>
  );
}
