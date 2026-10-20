import type { Meta, StoryObj } from "@storybook/react-vite";
import { useState } from "react";
import { Combobox } from "@angee/ui";

/**
 * The one option-list primitive (Base UI Combobox): a field-shaped trigger with
 * the value and caret, a popup with its search, grouped items with indicators
 * and the shared empty row. The combobox widget, many2many and the list search
 * box compose it; `Command` stays for the palette.
 */
const meta = {
  title: "Primitives/Combobox",
  component: Combobox.Root,
  parameters: { layout: "centered", owner: Combobox },
} satisfies Meta<typeof Combobox.Root>;

export default meta;

type Story = StoryObj;

const vendors: Record<string, string> = {
  cloudflare: "Cloudflare, Inc.",
  hetzner: "Hetzner Online GmbH",
  ovh: "OVH SAS",
  scaleway: "Scaleway SAS",
};
const items = Object.keys(vendors);

/** A single value with search. */
export const Default: Story = {
  render: function Single() {
    const [value, setValue] = useState<string | null>("cloudflare");
    return (
      <div className="w-72">
        <Combobox.Root items={items} value={value} onValueChange={(next) => setValue(next ?? null)}>
          <Combobox.Trigger aria-label="Vendor">
            <Combobox.Value>{(selected) => vendors[String(selected ?? "")] ?? "Pick a vendor"}</Combobox.Value>
            <Combobox.Icon />
          </Combobox.Trigger>
          <Combobox.Popup>
            <Combobox.Search />
            <Combobox.List>
              {(item: string) => (
                <Combobox.Item key={item} value={item}>
                  <Combobox.ItemText>{vendors[item]}</Combobox.ItemText>
                  <Combobox.ItemIndicator />
                </Combobox.Item>
              )}
            </Combobox.List>
            <Combobox.Empty />
          </Combobox.Popup>
        </Combobox.Root>
      </div>
    );
  },
};

/** Grouped items under labels. */
export const Grouped: Story = {
  render: function Grouped() {
    const [value, setValue] = useState<string | null>(null);
    const groups = [
      { label: "Hosting", items: ["cloudflare", "hetzner"] },
      { label: "Cloud", items: ["ovh", "scaleway"] },
    ];
    return (
      <div className="w-72">
        <Combobox.Root items={items} value={value} onValueChange={(next) => setValue(next ?? null)}>
          <Combobox.Trigger aria-label="Vendor">
            <Combobox.Value>{(selected) => vendors[String(selected ?? "")] ?? "Pick a vendor"}</Combobox.Value>
            <Combobox.Icon />
          </Combobox.Trigger>
          <Combobox.Popup>
            <Combobox.Search />
            <Combobox.List>
              {groups.map((group) => (
                <Combobox.Group key={group.label} items={group.items}>
                  <Combobox.GroupLabel>{group.label}</Combobox.GroupLabel>
                  <Combobox.Collection>
                    {(item: string) => (
                      <Combobox.Item key={item} value={item}>
                        <Combobox.ItemText>{vendors[item]}</Combobox.ItemText>
                        <Combobox.ItemIndicator />
                      </Combobox.Item>
                    )}
                  </Combobox.Collection>
                </Combobox.Group>
              ))}
            </Combobox.List>
            <Combobox.Empty />
          </Combobox.Popup>
        </Combobox.Root>
      </div>
    );
  },
};
