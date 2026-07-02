import {
  lazy,
  useEffect,
  useRef,
  type ReactElement,
  type ReactNode,
} from "react";

import { LazyBoundary } from "../fragments/LazyBoundary";
import { LoadingPanel } from "../fragments/LoadingPanel";
import { useUiT } from "../i18n";
import {
  DialogBackdrop,
  DialogContent,
  DialogPortal,
  DialogRoot,
} from "../ui/dialog";

// cmdk (and the command list it renders) loads on first ⌘K, not at boot.
const SpotlightCommandList = lazy(() => import("./SpotlightCommandList"));

export interface SpotlightCommand {
  id: string;
  title: ReactNode;
  searchValue?: string;
  hint?: ReactNode;
  icon?: ReactNode | string;
  group?: string;
  run: () => void | Promise<void>;
}

export interface SpotlightProps {
  commands: readonly SpotlightCommand[];
  onOpenChange: (open: boolean) => void;
  open: boolean;
  placeholder?: string;
}

export function useSpotlightShortcut(onToggle: () => void): void {
  // Keep the latest callback in a ref so the global listener mounts once —
  // callers can pass a fresh arrow each render without re-subscribing.
  const onToggleRef = useRef(onToggle);
  onToggleRef.current = onToggle;
  useEffect(() => {
    function onKeyDown(event: KeyboardEvent): void {
      if (event.repeat || event.isComposing) return;
      if (!(event.metaKey || event.ctrlKey) || event.key.toLowerCase() !== "k") {
        return;
      }
      if (isTextEntryTarget(event.target)) return;
      event.preventDefault();
      onToggleRef.current();
    }

    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);
}

export function Spotlight({
  commands,
  onOpenChange,
  open,
  placeholder,
}: SpotlightProps): ReactElement {
  const t = useUiT();
  const resolvedPlaceholder = placeholder ?? t("chrome.searchCommands");
  const commandPalette = t("chrome.commandPalette");

  return (
    <DialogRoot open={open} onOpenChange={onOpenChange}>
      <DialogPortal>
        <DialogBackdrop />
        <DialogContent aria-label={commandPalette} placement="default">
          {/* The dialog mounts its content only while open, so the lazy body —
              and cmdk with it — loads on first ⌘K. */}
          <LazyBoundary pending={<LoadingPanel />}>
            <SpotlightCommandList
              commands={commands}
              placeholder={resolvedPlaceholder}
              label={commandPalette}
              onOpenChange={onOpenChange}
            />
          </LazyBoundary>
        </DialogContent>
      </DialogPortal>
    </DialogRoot>
  );
}

function isTextEntryTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  return target.matches("input, textarea, select, [contenteditable='true']");
}
