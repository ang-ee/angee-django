import * as React from "react";
import { Command as CmdkCommand } from "cmdk";

import { Glyph } from "../chrome/Glyph";
import { cn } from "../lib/cn";
import { tv, type VariantProps } from "../lib/variants";
import { POPUP_ITEM, POPUP_LIST } from "./popover";

const COMMAND_ITEM = cn(
  POPUP_ITEM,
  "h-9 data-[disabled=true]:cursor-not-allowed data-[disabled=true]:opacity-60 data-[selected=true]:bg-inset [&_.glyph]:size-4 [&_.glyph]:text-fg-muted",
);

export const commandVariants = tv({
  slots: {
    root: "flex min-h-0 flex-col",
    search:
      "flex h-10 items-center gap-2 border-b border-border-subtle px-3 text-fg",
    inputIcon:
      "grid size-4 shrink-0 place-content-center text-fg-muted [&_.glyph]:size-3.5",
    input:
      "h-full min-w-0 flex-1 border-0 bg-transparent text-13 text-fg outline-none placeholder:text-fg-muted",
    list: cn(
      POPUP_LIST,
      "max-h-72 [&_[cmdk-list-sizer]]:flex [&_[cmdk-list-sizer]]:flex-col [&_[cmdk-list-sizer]]:gap-1",
    ),
    empty: "px-4 py-6 text-center text-13 text-fg-muted",
    group:
      "[&_[cmdk-group-heading]]:px-3 [&_[cmdk-group-heading]]:py-1.5 [&_[cmdk-group-heading]]:text-2xs [&_[cmdk-group-heading]]:font-semibold [&_[cmdk-group-heading]]:text-fg-muted [&_[cmdk-group-heading]]:uppercase",
    item: COMMAND_ITEM,
    separator: "my-1 h-px bg-border-subtle",
    loading: "px-4 py-3 text-13 text-fg-muted",
    shortcut: "ml-auto text-2xs text-fg-muted",
  },
});

export type CommandRecipeProps = VariantProps<typeof commandVariants>;

export type CommandProps = Omit<
  React.ComponentPropsWithoutRef<typeof CmdkCommand>,
  "className"
> & {
  className?: string;
};

export const CommandRoot = React.forwardRef<HTMLDivElement, CommandProps>(
  function CommandRoot({ className, ...props }, ref) {
    const styles = commandVariants();
    return (
      <CmdkCommand
        ref={ref}
        className={styles.root({ className })}
        {...props}
      />
    );
  },
);
CommandRoot.displayName = "CommandRoot";

export type CommandSearchProps = React.HTMLAttributes<HTMLDivElement> & {
  className?: string;
  icon?: React.ReactNode;
};

export const CommandSearch = React.forwardRef<
  HTMLDivElement,
  CommandSearchProps
>(function CommandSearch(
  { className, children, icon = <Glyph name="search" />, ...props },
  ref,
) {
  const styles = commandVariants();
  return (
    <div ref={ref} className={styles.search({ className })} {...props}>
      <span className={styles.inputIcon()}>{icon}</span>
      {children}
    </div>
  );
});
CommandSearch.displayName = "CommandSearch";

export type CommandInputProps = Omit<
  React.ComponentPropsWithoutRef<typeof CmdkCommand.Input>,
  "className"
> & {
  className?: string;
};

export const CommandInput = React.forwardRef<
  HTMLInputElement,
  CommandInputProps
>(function CommandInput({ className, ...props }, ref) {
  const styles = commandVariants();
  return (
    <CmdkCommand.Input
      ref={ref}
      className={styles.input({ className })}
      {...props}
    />
  );
});
CommandInput.displayName = "CommandInput";

export type CommandListProps = Omit<
  React.ComponentPropsWithoutRef<typeof CmdkCommand.List>,
  "className"
> & {
  className?: string;
};

export const CommandList = React.forwardRef<HTMLDivElement, CommandListProps>(
  function CommandList({ className, ...props }, ref) {
    const styles = commandVariants();
    return (
      <CmdkCommand.List
        ref={ref}
        className={styles.list({ className })}
        {...props}
      />
    );
  },
);
CommandList.displayName = "CommandList";

export type CommandEmptyProps = Omit<
  React.ComponentPropsWithoutRef<typeof CmdkCommand.Empty>,
  "className"
> & {
  className?: string;
};

export const CommandEmpty = React.forwardRef<
  HTMLDivElement,
  CommandEmptyProps
>(function CommandEmpty({ className, ...props }, ref) {
  const styles = commandVariants();
  return (
    <CmdkCommand.Empty
      ref={ref}
      className={styles.empty({ className })}
      {...props}
    />
  );
});
CommandEmpty.displayName = "CommandEmpty";

export type CommandGroupProps = Omit<
  React.ComponentPropsWithoutRef<typeof CmdkCommand.Group>,
  "className"
> & {
  className?: string;
};

export const CommandGroup = React.forwardRef<
  HTMLDivElement,
  CommandGroupProps
>(function CommandGroup({ className, ...props }, ref) {
  const styles = commandVariants();
  return (
    <CmdkCommand.Group
      ref={ref}
      className={styles.group({ className })}
      {...props}
    />
  );
});
CommandGroup.displayName = "CommandGroup";

export type CommandItemProps = Omit<
  React.ComponentPropsWithoutRef<typeof CmdkCommand.Item>,
  "className"
> & {
  className?: string;
};

export const CommandItem = React.forwardRef<HTMLDivElement, CommandItemProps>(
  function CommandItem({ className, ...props }, ref) {
    const styles = commandVariants();
    return (
      <CmdkCommand.Item
        ref={ref}
        className={styles.item({ className })}
        {...props}
      />
    );
  },
);
CommandItem.displayName = "CommandItem";

export type CommandSeparatorProps = Omit<
  React.ComponentPropsWithoutRef<typeof CmdkCommand.Separator>,
  "className"
> & {
  className?: string;
};

export const CommandSeparator = React.forwardRef<
  HTMLDivElement,
  CommandSeparatorProps
>(function CommandSeparator({ className, ...props }, ref) {
  const styles = commandVariants();
  return (
    <CmdkCommand.Separator
      ref={ref}
      className={styles.separator({ className })}
      {...props}
    />
  );
});
CommandSeparator.displayName = "CommandSeparator";

export type CommandLoadingProps = Omit<
  React.ComponentPropsWithoutRef<typeof CmdkCommand.Loading>,
  "className"
> & {
  className?: string;
};

export const CommandLoading = React.forwardRef<
  HTMLDivElement,
  CommandLoadingProps
>(function CommandLoading({ className, ...props }, ref) {
  const styles = commandVariants();
  return (
    <CmdkCommand.Loading
      ref={ref}
      className={styles.loading({ className })}
      {...props}
    />
  );
});
CommandLoading.displayName = "CommandLoading";

export type CommandShortcutProps = React.HTMLAttributes<HTMLSpanElement> & {
  className?: string;
};

export const CommandShortcut = React.forwardRef<
  HTMLSpanElement,
  CommandShortcutProps
>(function CommandShortcut({ className, ...props }, ref) {
  const styles = commandVariants();
  return (
    <span ref={ref} className={styles.shortcut({ className })} {...props} />
  );
});
CommandShortcut.displayName = "CommandShortcut";

export const Command = Object.assign(CommandRoot, {
  Root: CommandRoot,
  Search: CommandSearch,
  Input: CommandInput,
  List: CommandList,
  Empty: CommandEmpty,
  Group: CommandGroup,
  Item: CommandItem,
  Separator: CommandSeparator,
  Loading: CommandLoading,
  Shortcut: CommandShortcut,
});
