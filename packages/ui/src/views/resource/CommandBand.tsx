import type { ReactElement, ReactNode } from "react";

import { useUiT } from "../../i18n";
import { cn } from "../../lib/cn";
import { ControlBand } from "../../layouts/ControlBand";
import { ActionMenu } from "../../toolbars/ActionMenu";
import { RecordChrome, type RecordChromeContext } from "./record-chrome-context";
import { RecordPager, type RecordNavigation } from "./RecordPager";

// An item is a group's child that is neither `hidden` nor empty; a band with none renders no row.
const COMMAND_BAND_COLLAPSE = "not-has-[>div>:not([hidden]):not(:empty)]:hidden";

export interface CommandBandProps {
  /** Secondary verbs behind the leading icon-only menu (`ActionTrigger` children). */
  menu?: ReactNode;
  /** Record verbs shown after the menu; their action owner decides placement and primary emphasis. */
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
 * The record page's command band: `⋮` menu · record verbs · (space) · header
 * controls · pager, in the shell's control row. Pages with record commands
 * contribute to this one band, never a second bar. Read-only inspectors can
 * compose secondary actions and RecordChrome in RecordHeader.actions instead.
 *
 * The band takes no row while none of its items renders: when each group holds
 * only hidden or empty nodes (a contributed verb that offers nothing, an
 * `ActionMenu` with no item, chrome with no control), the layout's control row
 * collapses instead of showing an empty strip. It returns with the first item,
 * Save and Discard included.
 * @kind region
 * @region command-band
 */
export function CommandBand({ menu, verbs, chrome, navigation, end, blocked = false, className }: CommandBandProps): ReactElement {
  const t = useUiT();
  return (
    <ControlBand className={cn("overflow-x-auto overflow-y-hidden", COMMAND_BAND_COLLAPSE, className)}>
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
