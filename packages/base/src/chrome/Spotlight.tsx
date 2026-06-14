import {
  useEffect,
  useMemo,
  useRef,
  type ReactElement,
  type ReactNode,
} from "react";

import { useBaseT } from "../i18n";
import { Command } from "../ui/command";
import {
  DialogBackdrop,
  DialogBody,
  DialogContent,
  DialogPortal,
  DialogRoot,
  DialogTitle,
} from "../ui/dialog";
import { Kbd } from "../ui/kbd";
import { renderGlyph } from "./Glyph";

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
  const t = useBaseT();
  const resolvedPlaceholder = placeholder ?? t("chrome.searchCommands");
  const commandPalette = t("chrome.commandPalette");
  const groups = useMemo(
    () => groupCommands(commands, t("chrome.commands")),
    [commands, t],
  );

  function runCommand(command: SpotlightCommand): void {
    void Promise.resolve(command.run()).finally(() => onOpenChange(false));
  }

  return (
    <DialogRoot open={open} onOpenChange={onOpenChange}>
      <DialogPortal>
        <DialogBackdrop />
        <DialogContent aria-label={commandPalette} placement="default">
          <Command label={commandPalette} loop>
            <DialogTitle className="sr-only">{commandPalette}</DialogTitle>
            <Command.Search className="h-12 px-4">
              <Command.Input
                autoFocus
                placeholder={resolvedPlaceholder}
                aria-label={resolvedPlaceholder}
              />
              <Kbd>Esc</Kbd>
            </Command.Search>
            <DialogBody className="p-0">
              <Command.List className="max-h-modal-list-max-h">
                <Command.Empty>{t("chrome.noCommands")}</Command.Empty>
                {groups.map((group) => (
                  <Command.Group key={group.name} heading={group.name}>
                    {group.commands.map((command) => (
                      <Command.Item
                        key={command.id}
                        value={commandValue(command)}
                        onSelect={() => runCommand(command)}
                      >
                        {command.icon ? renderGlyph(command.icon) : null}
                        <span className="min-w-0 flex-1 truncate">
                          {command.title}
                        </span>
                        {command.hint ? (
                          <Command.Shortcut>{command.hint}</Command.Shortcut>
                        ) : null}
                      </Command.Item>
                    ))}
                  </Command.Group>
                ))}
              </Command.List>
            </DialogBody>
          </Command>
        </DialogContent>
      </DialogPortal>
    </DialogRoot>
  );
}

function groupCommands(
  commands: readonly SpotlightCommand[],
  defaultGroup: string,
): readonly {
  name: string;
  commands: readonly SpotlightCommand[];
}[] {
  const order: string[] = [];
  const byGroup = new Map<string, SpotlightCommand[]>();
  for (const command of commands) {
    const group = command.group ?? defaultGroup;
    const existing = byGroup.get(group);
    if (existing) {
      existing.push(command);
    } else {
      order.push(group);
      byGroup.set(group, [command]);
    }
  }
  return order.map((name) => ({ name, commands: byGroup.get(name) ?? [] }));
}

function commandValue(command: SpotlightCommand): string {
  if (command.searchValue) return command.searchValue;
  return typeof command.title === "string" ? command.title : command.id;
}

function isTextEntryTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  return target.matches("input, textarea, select, [contenteditable='true']");
}
