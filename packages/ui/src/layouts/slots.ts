import * as React from "react";

export type LayoutSlotComponent<
  TProps extends { children?: React.ReactNode },
> = ((props: TProps) => React.ReactElement) & {
  $$layoutSlot: symbol;
  displayName?: string;
};

export function createLayoutSlot<
  TProps extends { children?: React.ReactNode },
>(slot: symbol, displayName: string): LayoutSlotComponent<TProps> {
  const Component = (({ children }: TProps) =>
    React.createElement(React.Fragment, null, children)) as LayoutSlotComponent<TProps>;
  Component.$$layoutSlot = slot;
  Component.displayName = displayName;
  return Component;
}

export function findLayoutSlot<
  TProps extends { children?: React.ReactNode },
>(
  children: React.ReactNode,
  slot: symbol,
): React.ReactElement<TProps> | null {
  for (const child of React.Children.toArray(children)) {
    if (layoutSlotFor(child) === slot) {
      return child as React.ReactElement<TProps>;
    }
  }
  return null;
}

export function withoutLayoutSlots(
  children: React.ReactNode,
  slots: readonly symbol[],
): React.ReactNode[] {
  const blocked = new Set(slots);
  return React.Children.toArray(children).filter(
    (child) => !blocked.has(layoutSlotFor(child) ?? Symbol()),
  );
}

function layoutSlotFor(child: React.ReactNode): symbol | null {
  if (!React.isValidElement(child)) return null;
  const type = child.type as { $$layoutSlot?: symbol };
  return type.$$layoutSlot ?? null;
}
