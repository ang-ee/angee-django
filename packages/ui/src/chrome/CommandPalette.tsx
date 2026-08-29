import { useMemo, useState, type ReactElement } from "react";
import { useNavigate } from "@tanstack/react-router";

import { useUiT } from "../i18n";
import { Glyph } from "./Glyph";
import { useChromeMenuTree } from "./refine-menu";
import {
  Spotlight,
  useSpotlightShortcut,
  type SpotlightCommand,
} from "./Spotlight";

export interface CommandPaletteProps {
  /** Commands merged after the runtime nav commands (e.g. host actions). */
  commands?: readonly SpotlightCommand[];
  /** Dialog input placeholder (defaults to the trigger text). */
  placeholder?: string;
  /** Trigger button text (defaults to "Search commands..."). */
  triggerPlaceholder?: string;
}

/**
 * The live ⌘K command palette: a rail-styled trigger plus the {@link Spotlight}
 * dialog, seeded with one navigate command per menu destination. `commands`
 * appends host-supplied actions after the nav set.
 */
export function CommandPalette({
  commands,
  placeholder,
  triggerPlaceholder,
}: CommandPaletteProps): ReactElement {
  const t = useUiT();
  const [open, setOpen] = useState(false);
  useSpotlightShortcut(() => setOpen((current) => !current));
  const navCommands = useNavCommands();
  const allCommands = useMemo(
    () => (commands ? [...navCommands, ...commands] : navCommands),
    [navCommands, commands],
  );
  const triggerText = triggerPlaceholder ?? t("chrome.searchCommands");

  return (
    <>
      <button
        type="button"
        aria-label={t("chrome.openCommandPalette")}
        onClick={() => setOpen(true)}
        className="flex h-7 w-search-w min-w-0 cursor-text items-center gap-2 rounded-6 border border-transparent bg-rail-hi pl-3 pr-2 text-left text-13 text-on-rail-mut outline-none transition-colors hover:border-border-on-rail hover:bg-rail-hover focus-visible:focus-ring [&_.glyph]:size-3.5"
      >
        <Glyph name="search" />
        <span className="min-w-0 flex-1 truncate">{triggerText}</span>
        <kbd className="rounded-6 border border-kbd-on-rail-border bg-kbd-on-rail px-1.5 py-0.5 text-2xs font-medium text-on-rail-mut">
          ⌘K
        </kbd>
      </button>
      <Spotlight
        commands={allCommands}
        open={open}
        onOpenChange={setOpen}
        placeholder={placeholder ?? triggerText}
      />
    </>
  );
}

/** One navigate command per menu destination, grouped by its root app. */
function useNavCommands(): readonly SpotlightCommand[] {
  const menuTree = useChromeMenuTree();
  const navigate = useNavigate();
  return useMemo(
    () =>
      menuTree.navigableItems()
        .map(({ item, root, target }): SpotlightCommand => ({
          id: item.id,
          title: item.displayLabel,
          searchValue: `${root.displayLabel} ${item.displayLabel}`,
          icon: item.iconName,
          group: root.displayLabel,
          run: () => {
            void navigate({ to: target });
          },
        })),
    [menuTree, navigate],
  );
}
