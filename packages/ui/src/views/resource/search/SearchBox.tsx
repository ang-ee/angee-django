import * as React from "react";
import { useDebounce } from "use-debounce";
import { Glyph } from "../../../chrome/Glyph";
import { useUiT, type UiTranslate } from "../../../i18n";
import { cn } from "../../../lib/cn";
import { useMediaQuery } from "../../../lib/use-media-query";
import { Button } from "../../../ui/button";
import { CountBadge } from "../../../ui/badge";
import { RemovableChip } from "../../../ui/chip";
import { Combobox } from "../../../ui/combobox";
import { Input } from "../../../ui/input";
import { PopoverRoot, PopoverTrigger, PopoverPortal, PopoverPositioner, PopoverContent,
  POPUP_BASE, POPUP_ITEM, PORTALED_CONTROL_LAYER } from "../../../ui/popover";
import { textRoleVariants } from "../../../ui/text";
import { FilterClauseEditor } from "../../../toolbars/FilterClauseEditor";
import { QueryConditionEditor } from "../../../toolbars/ResourceConditionEditor";
import { useRelationOptions, useRelationSelectedOption } from "../../relation/relation-options";
import { labelText } from "../resource-view-utils";
import { GroupStackPanel, GroupLevelLabel, groupLevelLabel } from "./GroupStackPanel";
import type { ResourceSearch, SearchActiveItem, SearchFacet, SearchFacetOption } from "./types";

/** The three-column panel is 48rem wide; below this viewport width it stacks. */
const SEARCH_PANEL_ROOMY_QUERY = "(min-width: 52rem)";

type SearchSuggestion = { id: `suggest:${string}`; kind: "suggest"; label: string; apply: () => void };
type SearchItem = SearchActiveItem | SearchSuggestion;
type SuggestionGroup = { label: string; items: SearchSuggestion[] };
type RemoteFacetOptions = { facet: SearchFacet; options: readonly SearchFacetOption[] };

/** Presentation only: the model owns every active item and query transition. */
export function SearchBox({ search, box = true }: { search: ResourceSearch; box?: true | "collapsed" }): React.ReactElement {
  const [inputValue, setInputValue] = React.useState("");
  const [searchText] = useDebounce(inputValue.trim(), 250);
  const relations = search.catalog.facets.filter((facet) => facet.relation);
  return <RelationSuggestions facets={relations} searchText={searchText}>
    {(remote) => <SearchBoxContent search={search} box={box} remote={searchText === inputValue.trim() ? remote : []}
      inputValue={inputValue} setInputValue={setInputValue} />}
  </RelationSuggestions>;
}

// Each relation read is a component with a fixed hook topology. Render props
// collect native query results without copying them into a local option cache.
function RelationSuggestions({ facets, searchText, options = [], children }: {
  facets: readonly SearchFacet[];
  searchText: string;
  options?: readonly RemoteFacetOptions[];
  children: (options: readonly RemoteFacetOptions[]) => React.ReactNode;
}): React.ReactElement {
  const [facet, ...remaining] = facets;
  return facet ? <RelationSuggestionRead key={facet.field} facet={facet} searchText={searchText}>
    {(result) => <RelationSuggestions facets={remaining} searchText={searchText}
      options={[...options, result]}>{children}</RelationSuggestions>}
  </RelationSuggestionRead> : <>{children(options)}</>;
}

function RelationSuggestionRead({ facet, searchText, children }: {
  facet: SearchFacet; searchText: string; children: (options: RemoteFacetOptions) => React.ReactNode;
}): React.ReactElement {
  const result = useRelationOptions(facet.relation ?? null, { searchText, enabled: Boolean(searchText) });
  const options = result.options.flatMap((option) => {
    const bucket = facet.optionForValue?.(option.value, option.label);
    return bucket ? [bucket] : [];
  });
  const t = useUiT();
  return <>{children({ facet, options })}{searchText && result.list.error ? <p role="alert" className="text-xs text-danger-text">
    {result.list.error}<Button variant="ghost" size="sm" onClick={result.list.refetch}>{t("collection.retry")}</Button>
  </p> : null}</>;
}

function SearchBoxContent({ search, box, remote, inputValue, setInputValue }: {
  search: ResourceSearch; box: true | "collapsed"; remote: readonly RemoteFacetOptions[];
  inputValue: string; setInputValue: (value: string) => void;
}): React.ReactElement {
  const t = useUiT();
  // The panel is a popup: the viewport decides whether its three columns fit, not the box's own width.
  const roomy = useMediaQuery(SEARCH_PANEL_ROOMY_QUERY);
  const hostRef = React.useRef<HTMLDivElement>(null);
  const [panelOpen, setPanelOpen] = React.useState(false);
  const [suggestionsOpen, setSuggestionsOpen] = React.useState(false);
  const inputRef = React.useRef<HTMLInputElement>(null);
  const text = inputValue.trim();
  const groups = suggestionsFor(search, text, remote, t);
  const suggestions = groups.flatMap((group) => group.items);
  const collapsed = box === "collapsed";
  const active = search.active;
  const input = <Combobox.Chips aria-label={t("search.active")} className="flex min-w-0 flex-1 flex-wrap items-center gap-1">
    {active.map((item) => <Combobox.Chip key={item.id}
      render={<RemovableChip tone="brand" size="sm" className="max-w-64 focus-visible:focus-ring"
        removeLabel={item.kind === "group" ? groupLevelLabel(item, t) : activeLabel(item, t)}
        onRemove={() => { search.clear(item.id); inputRef.current?.focus(); }} />}>
      <ActiveLabel item={item} search={search} />
    </Combobox.Chip>)}
    <Combobox.Input ref={inputRef} aria-label={t("resourceToolbar.filterRecords")}
      placeholder={t("resourceToolbar.filterPlaceholder")}
      className="h-7 min-w-28 flex-1 border-0 bg-transparent text-13 text-fg outline-none placeholder:text-fg-muted" />
  </Combobox.Chips>;
  return <PopoverRoot open={panelOpen} onOpenChange={(open) => {
    setPanelOpen(open);
    setSuggestionsOpen(false);
    if (!open) setInputValue("");
  }}>
    <Combobox.Root<SearchItem, true> multiple autoHighlight
      value={[...active]} items={[...active, ...suggestions]} filteredItems={suggestions} filter={null}
      inputValue={inputValue} onInputValueChange={setInputValue}
      open={suggestionsOpen && Boolean(text)} onOpenChange={setSuggestionsOpen}
      isItemEqualToValue={(item, value) => item.id === value.id}
      itemToStringValue={(item) => item.id} itemToStringLabel={(item) => item.kind === "suggest" ? item.label : activeLabel(item, t)}
      onValueChange={(next, details) => {
        // Native Escape clears all selections when already closed. In a search
        // box Escape dismisses drafts/popups; explicit model commands clear facts.
        if (details.reason === "escape-key") { details.allowPropagation(); return; }
        active.filter((item) => !next.some((value) => value.id === item.id)).forEach((item) => search.clear(item.id));
        const added = next.filter((item): item is SearchSuggestion => item.kind === "suggest");
        added.forEach((item) => item.apply());
        if (added.length) { setInputValue(""); setSuggestionsOpen(false); }
      }}>
      <div ref={hostRef} className={cn("flex min-w-0 items-center gap-1 rounded-6",
        collapsed ? "shrink-0" : "min-h-8 flex-1 bg-inset px-2 py-0.5 focus-within:focus-ring")}>
        {!collapsed ? <><Glyph name="search" className="size-3.5 shrink-0 text-fg-muted" />{input}</> : null}
        <PopoverTrigger aria-label={t("search.panel")} className="inline-flex h-7 shrink-0 items-center justify-center gap-1 rounded-6 px-1 text-fg-muted outline-none hover:bg-sheet focus-visible:focus-ring">
          <Glyph name={collapsed ? "filter" : "chevron-down"} className="size-3.5" />
          {collapsed && active.length > 0 ? <CountBadge tone="brand">{active.length}</CountBadge> : null}
        </PopoverTrigger>
      </div>
      <Combobox.Portal><Combobox.Positioner sideOffset={4} className={PORTALED_CONTROL_LAYER}>
        <Combobox.Popup className={cn(POPUP_BASE, "max-h-80 min-w-[min(24rem,calc(100vw-1rem))] max-w-[calc(100vw-1rem)] overflow-y-auto p-1")}>
          <Combobox.List>
            {groups.map((group) => <Combobox.Group key={group.label} items={group.items}>
              <Combobox.GroupLabel className="px-2 py-1 text-2xs font-semibold text-fg-muted">{group.label}</Combobox.GroupLabel>
              <Combobox.Collection>{(item: SearchSuggestion) => <Combobox.Item key={item.id} value={item} className={POPUP_ITEM}>
                {item.label}
              </Combobox.Item>}</Combobox.Collection>
            </Combobox.Group>)}
          </Combobox.List>
        </Combobox.Popup>
      </Combobox.Positioner></Combobox.Portal>
      <PopoverPortal><PopoverPositioner anchor={hostRef} sideOffset={6} align="start">
        <PopoverContent aria-label={t("search.panel")} initialFocus={collapsed ? inputRef : undefined}
          className={cn("grid max-h-[min(36rem,calc(100dvh-5rem))] max-w-[calc(100vw-1rem)] overflow-y-auto overscroll-contain",
            roomy && !collapsed ? "w-[48rem] grid-cols-3" : "w-[min(22rem,calc(100vw-1rem))] grid-cols-1")}>
          <SearchPanel search={search} stacked={!roomy || collapsed} input={collapsed ? input : null} />
        </PopoverContent>
      </PopoverPositioner></PopoverPortal>
    </Combobox.Root>
  </PopoverRoot>;
}

function suggestionsFor(search: ResourceSearch, text: string, remote: readonly RemoteFacetOptions[], t: UiTranslate): SuggestionGroup[] {
  if (!text) return [];
  const { catalog } = search;
  const matches = (label: React.ReactNode) => (labelText(label) ?? "").toLocaleLowerCase().includes(text.toLocaleLowerCase());
  const suggest = (id: string, label: string, apply: () => void): SearchSuggestion => ({ id: `suggest:${id}`, kind: "suggest", label, apply });
  return [
    { label: t("search.textSuggestions"), items: catalog.text.map((field) => suggest(`text:${field.field}`,
      t("search.searchField", { field: labelText(field.label) ?? field.field, text }), () => search.setText(text, field.field))) },
    { label: t("resourceToolbar.filters"), items: [
      ...catalog.facets.flatMap((facet) => {
        const options = facet.relation || facet.source === "relation" ? remote.find((result) => result.facet.field === facet.field)?.options ?? []
          : facet.options.filter((option) => matches(option.label));
        return options.map((option) => suggest(`facet:${facet.field}:${option.id}`,
          t("search.facetSuggestion", { field: labelText(facet.label) ?? facet.field, value: labelText(option.label) ?? option.value ?? "" }),
          () => search.toggleFacetOption(facet.field, option.id, option)));
      }),
      ...catalog.filters.filter((filter) => matches(filter.label)).map((filter) => suggest(`filter:${filter.id}`,
        labelText(filter.label) ?? filter.id, () => search.toggleFilter(filter.id))),
    ] },
    { label: t("resourceToolbar.groupBy"), items: search.groupingEnabled
      && (search.maxGroupDepth === undefined || search.groupStack.length < search.maxGroupDepth)
      ? catalog.groups.filter((group) => matches(group.label) && !search.groupStack.some((level) => level.field === group.group.field
        && level.granularity === group.group.granularity)).map((group) => suggest(`group:${group.id}`,
        t("search.groupSuggestion", { field: labelText(group.label) ?? group.group.field }), () => search.addGroup(group.group))) : [] },
    { label: t("resourceToolbar.favorites"), items: catalog.favorites.filter((favorite) => matches(favorite.label))
      .map((favorite) => suggest(`favorite:${favorite.id}`, favorite.label, () => search.applyFavorite(favorite.id))) },
  ].filter((group) => group.items.length);
}

function activeLabel(item: SearchActiveItem, t: UiTranslate): string {
  const label = labelText(item.label) ?? item.id;
  switch (item.kind) {
    case "text": return `${label}: ${item.value}`;
    case "facet": return `${label}: ${item.options.map((option) => labelText(option.label) ?? option.value).join(` ${t("search.or")} `)}`;
    case "group": return `${t(item.index === 0 ? "resourceToolbar.groupBy" : "search.then")}: ${groupLevelLabel(item, t)}`;
    default: return label;
  }
}

function ActiveLabel({ item, search }: { item: SearchActiveItem; search: ResourceSearch }): React.ReactElement {
  const t = useUiT();
  if (item.kind === "group") return <>{t(item.index === 0 ? "resourceToolbar.groupBy" : "search.then")}: <GroupLevelLabel item={item} /></>;
  if (item.kind === "text") return <>{item.label}: {item.value}</>;
  if (item.kind === "favorite") return <><Glyph name="star" className="inline size-3" /> {item.label}</>;
  if (item.kind === "facet") {
    const facet = search.catalog.facets.find((facet) => facet.field === item.field);
    return <>{item.label}: {item.options.map((option, index) => <React.Fragment key={option.id}>
      {index > 0 ? ` ${t("search.or")} ` : null}
      {facet?.relation && option.value !== undefined && !facet.options.some((known) => known.id === option.id)
        ? <RelationSelectedLabel facet={facet} value={option.value} /> : option.label}
    </React.Fragment>)}</>;
  }
  return <>{item.label}</>;
}

function RelationSelectedLabel({ facet, value }: { facet: SearchFacet; value: string }): React.ReactElement {
  const selected = useRelationSelectedOption(facet.relation ?? null, value);
  return <>{selected?.label ?? value}</>;
}

function SearchPanel({ search, stacked, input }: { search: ResourceSearch; stacked: boolean; input: React.ReactNode }): React.ReactElement {
  const t = useUiT();
  const [clauseOpen, setClauseOpen] = React.useState(false);
  const [advancedOpen, setAdvancedOpen] = React.useState(false);
  const [favoriteOpen, setFavoriteOpen] = React.useState(false);
  const [editingFavoriteId, setEditingFavoriteId] = React.useState<string | null>(null);
  const [favoriteLabel, setFavoriteLabel] = React.useState(t("resourceToolbar.savedSearch"));
  const sections = new Map<string, typeof search.catalog.filters[number][]>();
  for (const option of search.catalog.filters) {
    const key = option.group ?? "";
    const choices = sections.get(key) ?? [];
    choices.push(option);
    sections.set(key, choices);
  }
  return <>
    <PickerColumn stacked={stacked} icon={<Glyph name="filter" className="size-3.5" />} title={t("resourceToolbar.filters")}>
      {input}
      {[...sections].map(([label, choices]) => <section key={label} aria-label={label || undefined} className="grid gap-1">
        {label ? <h4 className="px-2 pt-2 text-2xs font-semibold text-fg-muted">{label}</h4> : null}
        {choices.map((option) => <PickerButton key={option.id} active={search.active.some((item) => item.id === `filter:${option.id}`)}
          onClick={() => search.toggleFilter(option.id)}>{option.label}</PickerButton>)}
      </section>)}
      {search.catalog.facets.map((facet) => <section key={facet.field} className="grid gap-1">
        <h4 className="px-2 pt-2 text-2xs font-semibold text-fg-muted">{facet.label}</h4>
        {facet.options.map((option) => <PickerButton key={option.id}
          active={search.active.some((item) => item.kind === "facet" && item.field === facet.field && item.options.some((active) => active.id === option.id))}
          onClick={() => search.toggleFacetOption(facet.field, option.id)}>{option.label}</PickerButton>)}
      </section>)}
      {!search.catalog.filters.length && !search.catalog.facets.length ? <PickerMuted>{t("resourceToolbar.noFilters")}</PickerMuted> : null}
      <PickerDivider />
      <PickerButton active={clauseOpen} muted={!clauseOpen} onClick={() => setClauseOpen((open) => !open)}>
        <Glyph name="plus" className="size-3" />{t("resourceToolbar.addCustomFilter")}
      </PickerButton>
      {clauseOpen ? <FilterClauseEditor className="mt-2" fields={search.catalog.fields} onSubmit={search.addClause} /> : null}
      <PickerButton active={advancedOpen} muted={!advancedOpen} onClick={() => setAdvancedOpen((open) => !open)}>{t("search.advanced")}</PickerButton>
      {advancedOpen ? <QueryConditionEditor value={search.filter} fields={search.catalog.fields} readOnly={false} onChange={search.setFilter} /> : null}
    </PickerColumn>
    {search.groupingEnabled ? <PickerColumn stacked={stacked} icon={<Glyph name="sliders-horizontal" className="size-3.5" />} title={t("resourceToolbar.groupBy")}>
      <GroupStackPanel search={search} />
    </PickerColumn> : null}
    <PickerColumn stacked={stacked} icon={<Glyph name="star" className="size-3.5" />} title={t("resourceToolbar.favorites")}>
      {search.saveFavorite ? <PickerButton active={favoriteOpen} muted={!favoriteOpen} onClick={() => setFavoriteOpen((open) => !open)}>
        <Glyph name="plus" className="size-3" />{t("resourceToolbar.saveCurrentSearch")}
      </PickerButton> : null}
      {favoriteOpen ? <form className="mt-2 grid gap-2 rounded-6 border border-border-subtle bg-sheet p-2 shadow-xs" onSubmit={(event) => {
        event.preventDefault();
        if (!favoriteLabel.trim()) return;
        search.saveFavorite?.(favoriteLabel.trim());
        setFavoriteLabel(t("resourceToolbar.savedSearch")); setFavoriteOpen(false);
      }}>
        <Input size="sm" value={favoriteLabel} aria-label={t("resourceToolbar.favoriteName")} onChange={(event) => setFavoriteLabel(event.currentTarget.value)} />
        <Button type="submit" size="sm" variant="secondary" className="justify-center">{t("resourceToolbar.save")}</Button>
      </form> : null}
      {!search.catalog.favorites.length ? <PickerMuted>{t("resourceToolbar.noSavedSearches")}</PickerMuted> : search.catalog.favorites.map((favorite) => <div key={favorite.id} className="flex min-w-0 items-center gap-1">
        {editingFavoriteId === favorite.id ? <form className="flex min-w-0 gap-1" onSubmit={(event) => {
          event.preventDefault();
          if (!favoriteLabel.trim()) return;
          search.renameFavorite?.(favorite.id, favoriteLabel.trim()); setEditingFavoriteId(null);
        }}><Input size="sm" aria-label={t("resourceToolbar.favoriteName")} value={favoriteLabel} onChange={(event) => setFavoriteLabel(event.target.value)} />
          <Button type="submit" size="sm" variant="secondary">{t("resourceToolbar.save")}</Button></form>
          : <PickerButton active={search.active.some((item) => item.id === `favorite:${favorite.id}`)} onClick={() => search.applyFavorite(favorite.id)}>{favorite.label}</PickerButton>}
        {favorite.id.startsWith("favorite:") && search.pinFavorite ? <Button type="button" size="iconSm" variant="ghost"
          aria-label={t(favorite.pinned ? "resourceToolbar.unpinFavorite" : "resourceToolbar.pinFavorite")} aria-pressed={Boolean(favorite.pinned)}
          onClick={() => search.pinFavorite?.(favorite.id, !favorite.pinned)}><Glyph name="pin" /></Button> : null}
        {favorite.id.startsWith("favorite:") && search.renameFavorite ? <Button type="button" size="iconSm" variant="ghost" aria-label={t("resourceToolbar.renameFavorite")}
          onClick={() => { setFavoriteLabel(favorite.label); setEditingFavoriteId(favorite.id); }}><Glyph name="pencil" /></Button> : null}
      </div>)}
    </PickerColumn>
  </>;
}

function PickerColumn({ stacked, icon, title, children }: {
  stacked: boolean; icon: React.ReactNode; title: React.ReactNode; children: React.ReactNode;
}): React.ReactElement {
  return <section className={cn("min-w-0 p-3", stacked ? "border-b border-border-subtle last:border-b-0" : "border-r border-border-subtle last:border-r-0")}>
    <h3 className="mb-2 flex items-center gap-2 text-13 font-semibold text-fg"><span className="text-brand-soft-text">{icon}</span>{title}</h3>
    <div className="grid gap-1">{children}</div>
  </section>;
}
function PickerButton({ active = false, muted = false, children, onClick }: {
  active?: boolean; muted?: boolean; children: React.ReactNode; onClick?: () => void;
}): React.ReactElement {
  return <button type="button" className={cn("flex h-7 min-w-0 items-center gap-2 rounded-6 px-2 text-left text-13 outline-none transition-colors focus-visible:focus-ring",
    active ? "bg-brand-soft font-medium text-brand-soft-text" : muted ? "text-fg-muted hover:bg-inset hover:text-fg" : "text-fg hover:bg-inset")}
    aria-pressed={active} onClick={onClick}>{children}</button>;
}
function PickerDivider(): React.ReactElement { return <div className="my-1 border-t border-border-subtle" />; }
function PickerMuted({ children }: { children: React.ReactNode }): React.ReactElement {
  return <p className={cn(textRoleVariants({ role: "meta" }), "px-2 py-1")}>{children}</p>;
}
