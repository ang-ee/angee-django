import type { ReactElement, ReactNode } from "react";

import { useUiT } from "../../i18n";
import { cn } from "../../lib/cn";
import { ControlBand } from "../../layouts/ControlBand";
import { ActionMenu } from "../../toolbars/ActionMenu";
import { RecordChrome, type RecordChromeContext } from "./record-chrome-context";
import { RecordPager, type RecordNavigation } from "./RecordPager";

export interface CommandBandProps {
  /** Secondary verbs behind the leading icon-only menu (`ActionTrigger` children). */
  menu?: ReactNode;
  /** Lifecycle verbs shown as buttons after the menu: at most two, the first primary. */
  verbs?: ReactNode;
  /** The open record; renders the `form#chrome` controls (Needs you, Follow, Share) on the right. */
  chrome?: RecordChromeContext | null;
  /** The record's place in its collection; renders the pager last. */
  navigation?: RecordNavigation | null;
  /** Anything the page adds between the chrome controls and the pager (a save status). */
  end?: ReactNode;
  /** Blocks the menu while the record is dirty or pending. */
  blocked?: boolean;
  className?: string;
}

/**
 * The record page's command band: `⋮` menu · lifecycle verbs · (space) · header
 * controls · pager, in the shell's control row. Every record page renders this
 * one band; pages contribute verbs and the record, never a second bar.
 * @kind region
 * @region command-band
 */
export function CommandBand({ menu, verbs, chrome, navigation, end, blocked = false, className }: CommandBandProps): ReactElement {
  const t = useUiT();
  return (
    <ControlBand className={cn("overflow-x-auto overflow-y-hidden", className)}>
      <div className="flex min-w-max shrink-0 items-center gap-2">
        {menu ? (
          <ActionMenu label={null} aria-label={t("list.actions")} glyph="more-vertical" size="iconSm" blocked={blocked}>
            {menu}
          </ActionMenu>
        ) : null}
        {verbs}
      </div>
      <div className="min-w-2 flex-1" />
      <div className="flex min-w-max shrink-0 items-center gap-2">
        {chrome ? <RecordChrome value={chrome} /> : null}
        {end}
        {navigation ? <RecordPager navigation={navigation} /> : null}
      </div>
    </ControlBand>
  );
}
