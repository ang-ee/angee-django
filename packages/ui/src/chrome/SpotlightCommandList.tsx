import { useMemo, type ReactElement } from "react";

import { useUiT } from "../i18n";
import { Command } from "../ui/command";
import { DialogBody, DialogTitle } from "../ui/dialog";
import { Kbd } from "../ui/kbd";
import { renderGlyph } from "./Glyph";
import type { SpotlightCommand } from "./Spotlight";

export interface SpotlightCommandListProps {
  commands: readonly SpotlightCommand[];
  placeholder: string;
  /** Accessible name for the palette (also the sr-only dialog title). */
  label: string;
  onOpenChange: (open: boolean) => void;
}

/**
 * The cmdk-backed body of the {@link Spotlight} palette, code-split so cmdk loads
 * on first ⌘K rather than at boot. Mounted lazily inside the dialog shell only
 * while the palette is open.
 */
export default function SpotlightCommandList({
  commands,
  placeholder,
  label,
  onOpenChange,
}: SpotlightCommandListProps): ReactElement {
  const t = useUiT();
  const groups = useMemo(
    () => groupCommands(commands, t("chrome.commands")),
    [commands, t],
  );

  function runCommand(command: SpotlightCommand): void {
    void Promise.resolve(command.run()).finally(() => onOpenChange(false));
  }

  return (
    <Command label={label} loop>
      <DialogTitle className="sr-only">{label}</DialogTitle>
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
