import type { ComponentProps, ReactNode } from "react";
import {
  Button, DropdownMenu, Glyph, Tooltip, cn, textRoleVariants,
} from "@angee/ui";

/** Temporary drawing boundary, deliberately confined to this dated exploration. */
export function Spec({ owner, children, className }: {
  owner: string; children: ReactNode; className?: string;
}) {
  return <div data-spec={owner} className={cn("target-spec", className)}>
    {children}
    <span title={owner} aria-label={`Spec for ${owner}`} className={cn("target-spec-caption", textRoleVariants({ role: "caption" }))}>spec</span>
  </div>;
}

export function IconControl({ icon, label, children, ...props }: Omit<ComponentProps<typeof Button>, "children"> & {
  icon: string; label: string; children?: ReactNode;
}) {
  return <Tooltip label={label}>
    <Button size="iconSm" variant="ghost" aria-label={label} {...props}>
      <Glyph name={icon} />{children}
    </Button>
  </Tooltip>;
}

/** Only menu wiring is drawn; Base UI's shipped menu owns focus and keyboard behavior. */
export function ChoiceMenu({ label, value, items, onChange, children }: {
  label: string; value: string; items: readonly { value: string; label: string }[];
  onChange: (value: string) => void; children: ReactNode;
}) {
  return <DropdownMenu.Root>
    <DropdownMenu.Trigger render={<Button size="sm" variant="ghost" aria-label={label} className="min-w-0 gap-1 px-1" />}>
      {children}<Glyph name="chevron-down" />
    </DropdownMenu.Trigger>
    <DropdownMenu.Portal><DropdownMenu.Positioner align="start" sideOffset={4}>
      <DropdownMenu.Content className="min-w-48">
        <DropdownMenu.RadioGroup aria-label={label} value={value} onValueChange={onChange}>
          {items.map((item) => <DropdownMenu.RadioItem key={item.value} value={item.value}>
            <DropdownMenu.RadioItemIndicator />{item.label}
          </DropdownMenu.RadioItem>)}
        </DropdownMenu.RadioGroup>
      </DropdownMenu.Content>
    </DropdownMenu.Positioner></DropdownMenu.Portal>
  </DropdownMenu.Root>;
}
