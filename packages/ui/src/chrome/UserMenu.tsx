import { useEffect, useRef, useState, type ReactElement } from "react";
import { useNavigate } from "@tanstack/react-router";

import { useUiT } from "../i18n";
import { cn } from "../lib/cn";
import { useColorSchemePreference, type ColorScheme, type ColorSchemePreference } from "../lib/color-scheme";
import { useOptionalAppearance } from "../theme";
import { useContainer, useLoginPath, useRuntimeAuth, useRuntimeLogoutAction } from "../runtime";
import { ContainerOutlet } from "../lib/container-outlet";
import { avatarInitials } from "../ui/avatar";
import { DialogReturnFocusContext } from "../ui/dialog";
import {
  DropdownMenu,
  type DropdownMenuPositionerProps,
} from "../ui/dropdown-menu";
import { textRoleVariants } from "../ui/text";
import { Glyph } from "./Glyph";

export interface UserMenuProps {
  className?: string;
  side?: DropdownMenuPositionerProps["side"];
  align?: DropdownMenuPositionerProps["align"];
  sideOffset?: DropdownMenuPositionerProps["sideOffset"];
}


/**
 * @kind region
 * @region user-menu
 */
export function UserMenu({
  className,
  side = "bottom",
  align = "end",
  sideOffset = 8,
}: UserMenuProps): ReactElement {
  const t = useUiT();
  const { user, viewAs } = useRuntimeAuth();
  const [open, setOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  // A preview selected in the contributed picker leaves the menu so the
  // persistent banner can receive focus and expose its exit action.
  useEffect(() => { if (viewAs?.pending) setOpen(false); }, [viewAs?.pending]);
  const { logout, fetching } = useRuntimeLogoutAction();
  const loginPath = useLoginPath();
  const navigate = useNavigate();
  const appearance = useOptionalAppearance();
  const schemeDisabled = Boolean(viewAs?.viewAs || viewAs?.pending);
  const contributedItems = useContainer("shell#user-menu");
  const userMenu = t("chrome.userMenu");
  const displayName = user?.name || user?.username || t("chrome.userFallback");
  const email = user?.email;

  // The menu closes itself on item select; navigate once logout succeeds.
  async function signOut(): Promise<void> {
    if (await logout()) {
      void navigate({ to: loginPath });
    }
  }

  return (
    <DropdownMenu.Root open={open} onOpenChange={setOpen}>
      <DropdownMenu.Trigger
        ref={triggerRef}
        aria-label={userMenu}
        className={cn(
          "grid size-8 place-content-center rounded-6 border border-border-on-rail bg-avatar-default-bg text-2xs font-semibold uppercase text-on-brand outline-none transition-colors hover:bg-rail-hi focus-visible:focus-ring",
          className,
        )}
      >
        {avatarInitials(displayName)}
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal keepMounted>
        <DropdownMenu.Positioner side={side} align={align} sideOffset={sideOffset}>
          <DropdownMenu.Content aria-label={userMenu} className="w-60 text-13">
            <div className="border-b border-border-subtle px-3 pb-2 pt-1">
              <div className="truncate font-semibold text-fg">{displayName}</div>
              {email ? (
                <div className={textRoleVariants({ role: "caption", truncate: true })}>{email}</div>
              ) : null}
            </div>
            {appearance ? (
              <ColorSchemeItem
                resolved={appearance.colorScheme}
                onSelect={(next) => void appearance.setColorScheme(next)}
                disabled={schemeDisabled}
              />
            ) : <LocalColorSchemeItem disabled={schemeDisabled} />}
            <DialogReturnFocusContext.Provider value={triggerRef}>
              <ContainerOutlet entries={contributedItems} />
            </DialogReturnFocusContext.Provider>
            <DropdownMenu.Separator />
            <DropdownMenu.Item
              disabled={fetching}
              onClick={() => {
                void signOut();
              }}
            >
              <Glyph name="log-out" />
              <span className="flex-1 truncate">{t("chrome.signOut")}</span>
            </DropdownMenu.Item>
          </DropdownMenu.Content>
        </DropdownMenu.Positioner>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  );
}

/** The scheme toggle, named for the scheme it switches to. */
function ColorSchemeItem({ resolved, onSelect, disabled }: {
  resolved: ColorScheme;
  onSelect: (preference: ColorSchemePreference) => void;
  disabled: boolean;
}): ReactElement {
  const t = useUiT();
  const next: ColorSchemePreference = resolved === "dark" ? "light" : "dark";
  return (
    <DropdownMenu.Item disabled={disabled} onClick={() => onSelect(next)}>
      <Glyph name={next === "dark" ? "moon" : "sun"} />
      <span className="flex-1 truncate">
        {next === "dark" ? t("chrome.switchToDarkTheme") : t("chrome.switchToLightTheme")}
      </span>
    </DropdownMenu.Item>
  );
}

/**
 * The toggle over the browser-local scheme: mounting it reads, applies and follows that
 * preference, with the menu since its content stays mounted. Only a tree without an
 * appearance provider renders it, because a provider is the document scheme's only writer.
 */
function LocalColorSchemeItem({ disabled }: { disabled: boolean }): ReactElement {
  const { resolved, setPreference } = useColorSchemePreference();
  return <ColorSchemeItem resolved={resolved} onSelect={setPreference} disabled={disabled} />;
}
