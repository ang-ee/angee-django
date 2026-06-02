import {
  useEffect,
  useMemo,
  type ReactElement,
  type ReactNode,
} from "react";

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
import { Glyph } from "./Glyph";

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
  useEffect(() => {
    function onKeyDown(event: KeyboardEvent): void {
      if (event.repeat || event.isComposing) return;
      if (!(event.metaKey || event.ctrlKey) || event.key.toLowerCase() !== "k") {
        return;
      }
      if (isTextEntryTarget(event.target)) return;
      event.preventDefault();
      onToggle();
    }

    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onToggle]);
}

export function Spotlight({
  commands,
  onOpenChange,
  open,
  placeholder = "Search commands...",
}: SpotlightProps): ReactElement {
  const groups = useMemo(() => groupCommands(commands), [commands]);

  function runCommand(command: SpotlightCommand): void {
    void Promise.resolve(command.run()).finally(() => onOpenChange(false));
  }

  return (
    <DialogRoot open={open} onOpenChange={onOpenChange}>
      <DialogPortal>
        <DialogBackdrop />
        <DialogContent aria-label="Command palette" placement="default">
          <Command label="Command palette" loop>
            <DialogTitle className="sr-only">Command palette</DialogTitle>
            <Command.Search className="h-12 px-4">
              <Command.Input
                autoFocus
                placeholder={placeholder}
                aria-label={placeholder}
              />
              <Kbd>Esc</Kbd>
            </Command.Search>
            <DialogBody className="p-0">
              <Command.List className="max-h-modal-list-max-h">
                <Command.Empty>No matching commands.</Command.Empty>
                {groups.map((group) => (
                  <Command.Group key={group.name} heading={group.name}>
                    {group.commands.map((command) => (
                      <Command.Item
                        key={command.id}
                        value={commandValue(command)}
                        onSelect={() => runCommand(command)}
                      >
                        {command.icon ? renderCommandIcon(command.icon) : null}
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

function groupCommands(commands: readonly SpotlightCommand[]): readonly {
  name: string;
  commands: readonly SpotlightCommand[];
}[] {
  const order: string[] = [];
  const byGroup = new Map<string, SpotlightCommand[]>();
  for (const command of commands) {
    const group = command.group ?? "Commands";
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

function renderCommandIcon(icon: ReactNode | string): ReactNode {
  return typeof icon === "string" ? <Glyph name={icon} /> : icon;
}

function isTextEntryTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  return target.matches("input, textarea, select, [contenteditable='true']");
}
