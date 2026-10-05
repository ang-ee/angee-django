import * as React from "react";
import type { Meta, StoryObj } from "@storybook/react-vite";
import { expect, userEvent, waitFor, within } from "storybook/test";
import { Button, RemovableChip } from "@angee/ui";
import { useUiT } from "@angee/ui/i18n";
import { Combobox } from "@angee/ui/ui/combobox";

// Values and suggestions share identity, never label strings. The spike uses
// the installed primitive before any toolbar call site is switched.
type SearchItem = { id: string; kind: "active"; label: string }
  | { id: string; kind: "suggest"; label: string; apply: () => void };

function IdentitySpike() {
  const t = useUiT();
  const [active, setActive] = React.useState<SearchItem[]>([
    { id: "filter:open", kind: "active", label: "Open" },
    { id: "group:0", kind: "active", label: t("search.groupSuggestion", { field: "Status" }) },
  ]);
  const [inputValue, setInputValue] = React.useState("");
  const [expanded, setExpanded] = React.useState(false);
  const text = inputValue.trim();
  const suggestions: SearchItem[] = text ? [
    ...["Title", "Description"].map((field) => ({
      id: `suggest:text:${field}`, kind: "suggest" as const,
      label: t("search.searchField", { field, text }),
      apply: () => setActive((current) => [...current.filter((item) => item.id !== `text:${field}`),
        { id: `text:${field}`, kind: "active", label: `${field}: ${text}` }]),
    })),
    ...("Abandoned".toLowerCase().includes(text.toLowerCase()) ? [{
      id: "suggest:facet:abandoned", kind: "suggest" as const, label: t("search.facetSuggestion", { field: "Status", value: "Abandoned" }),
      apply: () => setActive((current) => [...current, { id: "facet:status", kind: "active", label: "Status: Abandoned" }]),
    }] : []),
  ] : [];
  const clear = (id: string) => setActive((current) => current.filter((item) => item.id !== id));
  return <div className="w-[min(40rem,100%)]">
    <Combobox.Root<SearchItem, true> multiple autoHighlight
      value={active} items={[...active, ...suggestions]} filteredItems={suggestions}
      filter={null} inputValue={inputValue} onInputValueChange={setInputValue}
      isItemEqualToValue={(item, value) => item.id === value.id}
      itemToStringValue={(item) => item.id} itemToStringLabel={(item) => item.label}
      onValueChange={(next, details) => {
        if (details.reason === "escape-key") return;
        active.filter((item) => !next.some((value) => value.id === item.id)).forEach((item) => clear(item.id));
        next.forEach((item) => { if (item.kind === "suggest") item.apply(); });
        if (next.some((item) => item.kind === "suggest")) setInputValue("");
      }}>
      <Combobox.Chips aria-label={t("search.active")} className="flex flex-wrap items-center gap-1 rounded-6 bg-inset p-2">
        {active.map((item) => <Combobox.Chip key={item.id}
          render={<RemovableChip tone="brand" size="sm" removeLabel={item.label} onRemove={() => clear(item.id)} />}>
          {item.label}
        </Combobox.Chip>)}
        {/* A regular trigger does not enter selected-value indexes. Omitting
            actual chips would break native focus and removal indexes. */}
        {active.length > 1 ? <Button variant="ghost" size="sm" aria-label={t("search.overflow", { count: active.length - 1 })}
          onClick={() => setExpanded((value) => !value)}>+{active.length - 1}</Button> : null}
        <Combobox.Input aria-label={t("resourceToolbar.filterRecords")} className="min-w-28 flex-1 bg-transparent outline-none" />
      </Combobox.Chips>
      {expanded ? <output aria-label={t("search.panel")}>{active.map((item) => item.label).join(", ")}</output> : null}
      <Combobox.Portal><Combobox.Positioner sideOffset={4} className="z-popover">
        <Combobox.Popup className="rounded-8 border border-border-subtle bg-popover p-2 shadow-popover">
          <Combobox.List>
            <Combobox.Group items={suggestions.slice(0, 2)}>
              <Combobox.GroupLabel>{t("search.textSuggestions")}</Combobox.GroupLabel>
              <Combobox.Collection>{(item: SearchItem) => <Combobox.Item key={item.id} value={item}
                className="rounded-6 p-2 data-[highlighted]:bg-inset">{item.label}</Combobox.Item>}</Combobox.Collection>
            </Combobox.Group>
            <Combobox.Group items={suggestions.slice(2)}>
              <Combobox.GroupLabel>{t("resourceToolbar.filters")}</Combobox.GroupLabel>
              <Combobox.Collection>{(item: SearchItem) => <Combobox.Item key={item.id} value={item}
                className="rounded-6 p-2 data-[highlighted]:bg-inset">{item.label}</Combobox.Item>}</Combobox.Collection>
            </Combobox.Group>
          </Combobox.List>
        </Combobox.Popup>
      </Combobox.Positioner></Combobox.Portal>
    </Combobox.Root>
  </div>;
}

const meta = { title: "Views/SearchBox", component: IdentitySpike } satisfies Meta<typeof IdentitySpike>;
export default meta;
export const ComboboxIdentitySpike: StoryObj<typeof meta> = {
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement), page = within(canvasElement.ownerDocument.body);
    const input = canvas.getByRole("combobox", { name: "Filter records" });
    await userEvent.click(canvas.getByRole("button", { name: "Show 1 more search items" }));
    await expect(canvas.getByLabelText("Search options")).toHaveTextContent("Open, Group by: Status");
    await userEvent.type(input, "ab");
    const options = await page.findAllByRole("option");
    await expect(options.map((option) => option.textContent)).toEqual([
      "Search Title for: ab", "Search Description for: ab", "Status: Abandoned",
    ]);
    await userEvent.keyboard("{Enter}");
    await canvas.findByRole("button", { name: "Remove Title: ab" });
    await expect(input).toHaveValue("");
    await userEvent.keyboard("{Backspace}");
    await waitFor(() => expect(canvas.queryByRole("button", { name: "Remove Title: ab" })).toBeNull());
    await userEvent.keyboard("{Escape}{ArrowLeft}{Delete}");
    await waitFor(() => expect(canvas.queryByRole("button", { name: "Remove Group by: Status" })).toBeNull());
    await expect(canvas.getByRole("button", { name: "Remove Open" })).toBeVisible();
  },
};
