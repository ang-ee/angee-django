import * as React from "react";
import type { ReactElement, ReactNode } from "react";
import { Glyph } from "../chrome/Glyph";
import { useBaseT } from "../i18n";
import { cn } from "../lib/cn";
import { titleCase } from "../lib/titleCase";
import { Button } from "../ui/button";
import { Chip } from "../ui/chip";
import { Input } from "../ui/input";
import {
  PopoverContent,
  PopoverPortal,
  PopoverPositioner,
  PopoverRoot,
  PopoverTrigger,
} from "../ui/popover";
import { Select } from "../ui/select";
import { Pager, type PagerState } from "../ui/pager";
import { textRoleVariants } from "../ui/text";
import type {
  ResourceViewFavorite,
  ResourceViewFilter,
  ResourceViewGroup,
  ResourceViewGroupGranularity,
  ResourceViewKind,
  ResourceViewLookupOperator,
} from "../views/resource-view-model";
import { resourceViewGroupsEqual } from "../views/resource-view-model";
import { groupFieldLabel } from "../views/ListInternals";

const FILTER_TEXT_COMMIT_DELAY_MS = 300;

export interface ResourceToolbarProps {
  pager: PagerState;
  view?: ResourceViewKind;
  group?: ResourceViewGroup | null;
  groupStack?: readonly ResourceViewGroup[];
  groupOptions?: readonly ResourceToolbarGroupOption[];
  filterOptions?: readonly ResourceToolbarFilterOption[];
  filterFields?: readonly ResourceToolbarFilterField[];
  customFilterChips?: readonly ResourceToolbarCustomFilterChip[];
  favorites?: readonly ResourceViewFavorite[];
  activeFilterIds?: readonly string[];
  filterText?: string;
  createLabel?: ReactNode;
  onCreate?: () => void;
  /** Extra controls rendered in the toolbar's leading slot, beside the filter. */
  actions?: ReactNode;
  /** Trailing control rendered on the right (e.g. a List/Grid layout switcher). */
  viewSwitcher?: ReactNode;
  onFilterTextChange?: (value: string) => void;
  onFilterToggle?: (id: string) => void;
  onClearGroup?: () => void;
  onGroupStackChange?: (groups: readonly ResourceViewGroup[]) => void;
  onPageChange?: (page: number) => void;
  onPageSizeChange?: (pageSize: number) => void;
  onViewChange?: (view: ResourceViewKind) => void;
  onCustomFilterAdd?: (filter: ResourceToolbarCustomFilter) => void;
  onCustomFilterRemove?: (id: string) => void;
  onFavoriteSave?: (label: string) => void;
  onFavoriteSelect?: (favorite: ResourceViewFavorite) => void;
  pagerSubject?: string;
  pagerTotalUnit?: string;
  className?: string;
}

export interface ResourceToolbarFilterOption {
  id: string;
  label: ReactNode;
  chipLabel?: ReactNode;
  filter: ResourceViewFilter;
}

export interface ResourceToolbarGroupOption {
  id: string;
  label: ReactNode;
  group: ResourceViewGroup;
  type?: "date" | "value";
  granularities?: readonly ResourceViewGroupGranularity[];
}

export type ResourceToolbarFilterFieldType =
  | "text"
  | "number"
  | "date"
  | "datetime"
  | "selection"
  | "boolean";

export type ResourceToolbarCustomFilterOperator =
  | ResourceViewLookupOperator
  | "isNotNull";

export interface ResourceToolbarFilterChoice {
  value: string;
  label: ReactNode;
}

export interface ResourceToolbarFilterField {
  id: string;
  field?: string;
  label: ReactNode;
  type?: ResourceToolbarFilterFieldType;
  options?: readonly ResourceToolbarFilterChoice[];
  operators?: readonly ResourceToolbarCustomFilterOperator[];
}

export interface ResourceToolbarCustomFilter {
  field: string;
  operator: ResourceToolbarCustomFilterOperator;
  value?: string | number | boolean;
  type?: ResourceToolbarFilterFieldType;
}

export interface ResourceToolbarCustomFilterChip {
  id: string;
  label: ReactNode;
}

export interface ResourceViewSwitcherProps {
  view: ResourceViewKind;
  onViewChange?: (view: ResourceViewKind) => void;
  ariaLabel?: string;
  className?: string;
}

export function ResourceToolbar({
  pager,
  view,
  group,
  groupStack,
  groupOptions,
  filterOptions = [],
  filterFields = [],
  customFilterChips = [],
  favorites = [],
  activeFilterIds = [],
  filterText = "",
  createLabel,
  onCreate,
  actions,
  viewSwitcher,
  onFilterToggle,
  onFilterTextChange,
  onClearGroup,
  onGroupStackChange,
  onPageChange,
  onPageSizeChange,
  onViewChange,
  onCustomFilterAdd,
  onCustomFilterRemove,
  onFavoriteSave,
  onFavoriteSelect,
  pagerSubject,
  pagerTotalUnit,
  className,
}: ResourceToolbarProps): ReactElement {
  const t = useBaseT();
  const resolvedCreateLabel = createLabel ?? t("dataToolbar.create");
  const groupControls =
    groupOptions !== undefined
    || groupStack !== undefined
    || group !== undefined
    || onGroupStackChange !== undefined
    || onClearGroup !== undefined;
  const toolbarGroupOptions = groupOptions ?? [];
  const groups = groupControls ? groupStack ?? (group ? [group] : []) : [];
  const activeFilters = filterOptions.filter((option) =>
    activeFilterIds.includes(option.id),
  );
  return (
    <section
      aria-label={t("dataToolbar.controls")}
      className={cn(
        "flex min-h-11 items-center gap-2 border-b border-border-subtle bg-sheet px-3 py-2",
        className,
      )}
    >
      {onCreate ? (
        <Button type="button" variant="primary" size="sm" onClick={onCreate}>
          <Glyph name="plus" className="glyph" />
          {resolvedCreateLabel}
        </Button>
      ) : null}
      {actions}
      <FilterPicker
        groups={groups}
        groupControls={groupControls}
        groupOptions={toolbarGroupOptions}
        activeFilters={activeFilters}
        activeFilterIds={activeFilterIds}
        filterOptions={filterOptions}
        filterFields={filterFields}
        customFilterChips={customFilterChips}
        favorites={favorites}
        filterText={filterText}
        onClearGroup={onClearGroup}
        onFilterTextChange={onFilterTextChange}
        onFilterToggle={onFilterToggle}
        onGroupStackChange={onGroupStackChange}
        onCustomFilterAdd={onCustomFilterAdd}
        onCustomFilterRemove={onCustomFilterRemove}
        onFavoriteSave={onFavoriteSave}
        onFavoriteSelect={onFavoriteSelect}
      />
      <div className="min-w-2 flex-1" />
      <Pager
        {...pager}
        subject={pagerSubject}
        unit={pagerTotalUnit}
        onPageChange={onPageChange}
        onPageSizeChange={onPageSizeChange}
      />
      {view && onViewChange ? (
        <ResourceViewSwitcher view={view} onViewChange={onViewChange} />
      ) : null}
      {viewSwitcher}
    </section>
  );
}

function FilterPicker({
  groups,
  groupControls,
  groupOptions,
  filterOptions,
  filterFields,
  customFilterChips,
  favorites,
  activeFilters,
  activeFilterIds,
  filterText,
  onFilterTextChange,
  onFilterToggle,
  onClearGroup,
  onGroupStackChange,
  onCustomFilterAdd,
  onCustomFilterRemove,
  onFavoriteSave,
  onFavoriteSelect,
}: {
  groups: readonly ResourceViewGroup[];
  groupControls: boolean;
  groupOptions: readonly ResourceToolbarGroupOption[];
  filterOptions: readonly ResourceToolbarFilterOption[];
  filterFields: readonly ResourceToolbarFilterField[];
  customFilterChips: readonly ResourceToolbarCustomFilterChip[];
  favorites: readonly ResourceViewFavorite[];
  activeFilters: readonly ResourceToolbarFilterOption[];
  activeFilterIds: readonly string[];
  filterText: string;
  onFilterTextChange?: (value: string) => void;
  onFilterToggle?: (id: string) => void;
  onClearGroup?: () => void;
  onGroupStackChange?: (groups: readonly ResourceViewGroup[]) => void;
  onCustomFilterAdd?: (filter: ResourceToolbarCustomFilter) => void;
  onCustomFilterRemove?: (id: string) => void;
  onFavoriteSave?: (label: string) => void;
  onFavoriteSelect?: (favorite: ResourceViewFavorite) => void;
}): ReactElement {
  const t = useBaseT();
  const defaultFavoriteLabel = t("dataToolbar.savedSearch");
  const [customFilterOpen, setCustomFilterOpen] = React.useState(false);
  const [customFieldId, setCustomFieldId] = React.useState("");
  const [customOperator, setCustomOperator] =
    React.useState<ResourceToolbarCustomFilterOperator>("contains");
  const [customValue, setCustomValue] = React.useState("");
  const selectedCustomField =
    filterFields.find((field) => field.id === customFieldId) ?? filterFields[0];
  const effectiveCustomOperator = operatorForField(
    selectedCustomField,
    customOperator,
  );
  const [customGroupOpen, setCustomGroupOpen] = React.useState(false);
  const [customGroupId, setCustomGroupId] = React.useState("");
  const [customGroupGranularity, setCustomGroupGranularity] =
    React.useState<ResourceViewGroupGranularity>("day");
  const selectedCustomGroup =
    groupOptions.find((option) => option.id === customGroupId) ?? groupOptions[0];
  const [favoriteOpen, setFavoriteOpen] = React.useState(false);
  const [favoriteLabel, setFavoriteLabel] =
    React.useState(defaultFavoriteLabel);
  const [draftFilterText, setDraftFilterText] = React.useState(filterText);

  React.useEffect(() => {
    setDraftFilterText(filterText);
  }, [filterText]);

  const commitFilterText = React.useCallback(
    (value: string) => {
      if (value !== filterText) onFilterTextChange?.(value);
    },
    [filterText, onFilterTextChange],
  );

  React.useEffect(() => {
    if (draftFilterText === filterText) return undefined;
    const timeout = window.setTimeout(
      () => commitFilterText(draftFilterText),
      FILTER_TEXT_COMMIT_DELAY_MS,
    );
    return () => window.clearTimeout(timeout);
  }, [commitFilterText, draftFilterText, filterText]);

  function addCustomFilter() {
    if (!selectedCustomField || !onCustomFilterAdd) return;
    const needsValue = customFilterNeedsValue(effectiveCustomOperator);
    const value = needsValue
      ? coerceFilterValue(selectedCustomField, customValue)
      : undefined;
    if (needsValue && value === undefined) return;
    onCustomFilterAdd({
      field: selectedCustomField.field ?? selectedCustomField.id,
      operator: effectiveCustomOperator,
      ...(value !== undefined ? { value } : {}),
      ...(selectedCustomField.type ? { type: selectedCustomField.type } : {}),
    });
    setCustomValue("");
    setCustomFilterOpen(false);
  }

  function addCustomGroup() {
    if (!selectedCustomGroup || !onGroupStackChange) return;
    const group =
      selectedCustomGroup.type === "date"
        ? { ...selectedCustomGroup.group, granularity: customGroupGranularity }
        : selectedCustomGroup.group;
    onGroupStackChange([...groups, group]);
    setCustomGroupOpen(false);
  }

  function saveFavorite() {
    const label = favoriteLabel.trim();
    if (!label || !onFavoriteSave) return;
    onFavoriteSave(label);
    setFavoriteLabel(defaultFavoriteLabel);
    setFavoriteOpen(false);
  }

  return (
    <PopoverRoot>
      <div className="inline-flex h-8 min-w-0 max-w-xl flex-1 items-center gap-1 overflow-hidden rounded-6 border border-transparent bg-inset pl-2 pr-1 text-13 text-fg focus-within:border-border-focus focus-within:bg-sheet focus-within:focus-ring">
        <Glyph name="search" className="size-3.5 shrink-0 text-fg-muted" />
        {groups.map((nextGroup, index) => (
          <FacetChip
            key={`${nextGroup.field}:${nextGroup.granularity ?? ""}`}
            label={index === 0 ? t("dataToolbar.groupBy") : t("dataToolbar.then")}
            value={groupLabel(nextGroup)}
            removeLabel={
              index === 0
                ? t("dataToolbar.removeGroup")
                : t("dataToolbar.removeGroupLevel")
            }
            onRemove={() => {
              const next = groups.filter((_, groupIndex) => groupIndex !== index);
              if (next.length === 0) onClearGroup?.();
              else onGroupStackChange?.(next);
            }}
          />
        ))}
        {activeFilters.map((option) => (
          <FacetChip
            key={option.id}
            label={t("dataToolbar.filter")}
            value={option.chipLabel ?? option.label}
            removeLabel={t("dataToolbar.remove", {
              label: String(option.chipLabel ?? option.label),
            })}
            onRemove={() => onFilterToggle?.(option.id)}
          />
        ))}
        {customFilterChips.map((chip) => (
          <FacetChip
            key={chip.id}
            label={t("dataToolbar.filter")}
            value={chip.label}
            removeLabel={t("dataToolbar.remove", {
              label: labelText(chip.label) ?? t("dataToolbar.filterFallback"),
            })}
            onRemove={() => onCustomFilterRemove?.(chip.id)}
          />
        ))}
        <input
          type="search"
          value={draftFilterText}
          placeholder={t("dataToolbar.filterPlaceholder")}
          aria-label={t("dataToolbar.filterRecords")}
          className="h-full min-w-[7rem] flex-1 border-0 bg-transparent text-13 text-fg outline-none placeholder:text-fg-muted"
          onBlur={(event) => commitFilterText(event.currentTarget.value)}
          onChange={(event) => setDraftFilterText(event.currentTarget.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              commitFilterText(event.currentTarget.value);
            }
          }}
        />
        <PopoverTrigger
          className="grid size-6 shrink-0 place-content-center rounded-6 text-fg-muted outline-none transition-colors hover:bg-sheet hover:text-fg focus-visible:focus-ring"
          aria-label={
            groupControls
              ? t("dataToolbar.filterGroupFavorites")
              : t("dataToolbar.filterAndFavorites")
          }
        >
          <Glyph name="chevron-down" className="size-3" />
        </PopoverTrigger>
      </div>
      <PopoverPortal>
        <PopoverPositioner sideOffset={6} align="start">
          <PopoverContent
            className={cn(
              "grid max-w-[calc(100vw-2rem)]",
              groupControls ? "w-[45rem] grid-cols-3" : "w-[30rem] grid-cols-2",
            )}
          >
            <PickerColumn
              icon={<Glyph name="filter" className="size-3.5" />}
              title={t("dataToolbar.filters")}
            >
              {filterOptions.length === 0 ? (
                <PickerMuted>{t("dataToolbar.noFilters")}</PickerMuted>
              ) : (
                filterOptions.map((option) => (
                  <PickerButton
                    key={option.id}
                    active={activeFilterIds.includes(option.id)}
                    onClick={() => onFilterToggle?.(option.id)}
                  >
                    {option.label}
                  </PickerButton>
                ))
              )}
              <PickerDivider />
              <PickerButton
                active={customFilterOpen}
                muted={!customFilterOpen}
                onClick={() => setCustomFilterOpen((value) => !value)}
              >
                <Glyph name="plus" className="size-3" />
                {t("dataToolbar.addCustomFilter")}
              </PickerButton>
              {customFilterOpen ? (
                <CustomFilterEditor
                  fields={filterFields}
                  field={selectedCustomField}
                  fieldId={selectedCustomField?.id ?? ""}
                  operator={effectiveCustomOperator}
                  value={customValue}
                  onField={(id) => {
                    const nextField = filterFields.find((field) =>
                      field.id === id);
                    setCustomFieldId(id);
                    setCustomOperator(defaultOperator(nextField));
                    setCustomValue("");
                  }}
                  onOperator={setCustomOperator}
                  onValue={setCustomValue}
                  onAdd={addCustomFilter}
                />
              ) : null}
            </PickerColumn>
            {groupControls ? (
              <PickerColumn
                icon={<Glyph name="sliders-horizontal" className="size-3.5" />}
                title={t("dataToolbar.groupBy")}
              >
                {groupOptions.map((option) => (
                  <GroupOptionButton
                    key={option.id}
                    option={option}
                    groups={groups}
                    onGroupStackChange={onGroupStackChange}
                  />
                ))}
                <PickerDivider />
                <PickerButton
                  active={customGroupOpen}
                  muted={!customGroupOpen}
                  onClick={() => setCustomGroupOpen((value) => !value)}
                >
                  <Glyph name="plus" className="size-3" />
                  {t("dataToolbar.addCustomGroup")}
                </PickerButton>
                {customGroupOpen ? (
                  <CustomGroupEditor
                    options={groupOptions}
                    option={selectedCustomGroup}
                    optionId={selectedCustomGroup?.id ?? ""}
                    granularity={customGroupGranularity}
                    onOption={(id) => {
                      const option = groupOptions.find((item) => item.id === id);
                      setCustomGroupId(id);
                      setCustomGroupGranularity(
                        option?.group.granularity ?? "day",
                      );
                    }}
                    onGranularity={setCustomGroupGranularity}
                    onAdd={addCustomGroup}
                  />
                ) : null}
              </PickerColumn>
            ) : null}
            <PickerColumn
              icon={<Glyph name="star" className="size-3.5" />}
              title={t("dataToolbar.favorites")}
            >
              <PickerButton
                active={favoriteOpen}
                muted={!favoriteOpen}
                onClick={() => setFavoriteOpen((value) => !value)}
              >
                <Glyph name="plus" className="size-3" />
                {t("dataToolbar.saveCurrentSearch")}
              </PickerButton>
              {favoriteOpen ? (
                <form
                  className="mt-2 grid gap-2 rounded-6 border border-border-subtle bg-sheet p-2 shadow-xs"
                  onSubmit={(event) => {
                    event.preventDefault();
                    saveFavorite();
                  }}
                >
                  <Input
                    size="sm"
                    value={favoriteLabel}
                    aria-label={t("dataToolbar.favoriteName")}
                    onChange={(event) =>
                      setFavoriteLabel(event.currentTarget.value)}
                  />
                  <Button
                    type="submit"
                    size="sm"
                    variant="secondary"
                    className="justify-center"
                  >
                    {t("dataToolbar.save")}
                  </Button>
                </form>
              ) : null}
              {favorites.length === 0 ? (
                <PickerMuted>{t("dataToolbar.noSavedSearches")}</PickerMuted>
              ) : (
                favorites.map((favorite) => (
                  <PickerButton
                    key={favorite.id}
                    onClick={() => onFavoriteSelect?.(favorite)}
                  >
                    {favorite.label}
                  </PickerButton>
                ))
              )}
            </PickerColumn>
          </PopoverContent>
        </PopoverPositioner>
      </PopoverPortal>
    </PopoverRoot>
  );
}

function FacetChip({
  label,
  value,
  removeLabel,
  onRemove,
}: {
  label: ReactNode;
  value: ReactNode;
  removeLabel: string;
  onRemove: () => void;
}): ReactElement {
  return (
    <Chip tone="brand" size="sm" className="max-w-52 gap-1">
      <span className="shrink-0">{label}:</span>
      <span className="truncate">{value}</span>
      <button
        type="button"
        aria-label={removeLabel}
        className="ml-0.5 rounded-full text-brand-soft-text outline-none hover:bg-on-brand-soft-hover focus-visible:focus-ring"
        onClick={onRemove}
      >
        <Glyph name="x" className="size-3" />
      </button>
    </Chip>
  );
}

function PickerColumn({
  icon,
  title,
  children,
}: {
  icon: ReactNode;
  title: ReactNode;
  children: ReactNode;
}): ReactElement {
  return (
    <section className="min-w-0 border-r border-border-subtle p-3 last:border-r-0">
      <h3 className="mb-2 flex items-center gap-2 text-13 font-semibold text-fg">
        <span className="text-brand-soft-text">{icon}</span>
        {title}
      </h3>
      <div className="grid gap-1">{children}</div>
    </section>
  );
}

function PickerButton({
  active = false,
  muted = false,
  children,
  onClick,
}: {
  active?: boolean;
  muted?: boolean;
  children: ReactNode;
  onClick?: () => void;
}): ReactElement {
  return (
    <button
      type="button"
      className={cn(
        "flex h-7 min-w-0 items-center gap-2 rounded-6 px-2 text-left text-13 outline-none transition-colors focus-visible:focus-ring",
        active
          ? "bg-brand-soft font-medium text-brand-soft-text"
          : muted
            ? "text-fg-muted hover:bg-inset hover:text-fg"
            : "text-fg hover:bg-inset",
      )}
      onClick={onClick}
    >
      {children}
    </button>
  );
}

function CustomFilterEditor({
  fields,
  field,
  fieldId,
  operator,
  value,
  onField,
  onOperator,
  onValue,
  onAdd,
}: {
  fields: readonly ResourceToolbarFilterField[];
  field: ResourceToolbarFilterField | undefined;
  fieldId: string;
  operator: ResourceToolbarCustomFilterOperator;
  value: string;
  onField: (id: string) => void;
  onOperator: (operator: ResourceToolbarCustomFilterOperator) => void;
  onValue: (value: string) => void;
  onAdd: () => void;
}): ReactElement {
  const t = useBaseT();
  const operators = operatorsForField(field);
  const needsValue = customFilterNeedsValue(operator);
  return (
    <div className="mt-2 grid gap-2 rounded-6 border border-border-subtle bg-sheet p-2 shadow-xs">
      {fields.length === 0 ? (
        <PickerMuted>{t("dataToolbar.noFilterFields")}</PickerMuted>
      ) : (
        <>
          <Select
            size="sm"
            value={fieldId}
            aria-label={t("dataToolbar.filterField")}
            options={fields.map((item) => ({
              value: item.id,
              label: item.label,
            }))}
            onValueChange={onField}
          />
          <div className="flex min-w-0 gap-2">
            <Select
              size="sm"
              value={operator}
              className="min-w-0 flex-1"
              aria-label={t("dataToolbar.filterOperator")}
              options={operators.map((item) => ({
                value: item,
                label: FILTER_OPERATOR_LABEL[item],
              }))}
              onValueChange={(next) =>
                onOperator(next as ResourceToolbarCustomFilterOperator)}
            />
            {needsValue ? (
              field?.options ? (
                <Select
                  size="sm"
                  value={value}
                  className="min-w-0 flex-1"
                  aria-label={t("dataToolbar.filterValue")}
                  placeholder={t("dataToolbar.value")}
                  options={field.options.map((option) => ({
                    value: option.value,
                    label: option.label,
                  }))}
                  onValueChange={onValue}
                />
              ) : (
                <Input
                  size="sm"
                  type={filterInputType(field)}
                  value={value}
                  placeholder={t("dataToolbar.value")}
                  aria-label={t("dataToolbar.filterValue")}
                  className="min-w-0 flex-1"
                  onChange={(event) => onValue(event.currentTarget.value)}
                />
              )
            ) : null}
          </div>
          <Button
            type="button"
            size="sm"
            variant="secondary"
            className="justify-center"
            disabled={!field || (needsValue && value.trim() === "")}
            onClick={onAdd}
          >
            {t("dataToolbar.add")}
          </Button>
        </>
      )}
    </div>
  );
}

function CustomGroupEditor({
  options,
  option,
  optionId,
  granularity,
  onOption,
  onGranularity,
  onAdd,
}: {
  options: readonly ResourceToolbarGroupOption[];
  option: ResourceToolbarGroupOption | undefined;
  optionId: string;
  granularity: ResourceViewGroupGranularity;
  onOption: (id: string) => void;
  onGranularity: (granularity: ResourceViewGroupGranularity) => void;
  onAdd: () => void;
}): ReactElement {
  const t = useBaseT();
  const granularities = option?.granularities ?? DEFAULT_GRANULARITIES;
  return (
    <div className="mt-2 grid gap-2 rounded-6 border border-border-subtle bg-sheet p-2 shadow-xs">
      {options.length === 0 ? (
        <PickerMuted>{t("dataToolbar.noGroupFields")}</PickerMuted>
      ) : (
        <>
          <Select
            size="sm"
            value={optionId}
            aria-label={t("dataToolbar.groupField")}
            options={options.map((item) => ({
              value: item.id,
              label: item.label,
            }))}
            onValueChange={onOption}
          />
          {option?.type === "date" ? (
            <Select
              size="sm"
              value={granularity}
              aria-label={t("dataToolbar.groupGranularity")}
              options={granularities.map((item) => ({
                value: item,
                label: titleCase(item),
              }))}
              onValueChange={(next) =>
                onGranularity(next as ResourceViewGroupGranularity)}
            />
          ) : null}
          <Button
            type="button"
            size="sm"
            variant="secondary"
            className="justify-center"
            onClick={onAdd}
          >
            {t("dataToolbar.add")}
          </Button>
        </>
      )}
    </div>
  );
}

function GroupOptionButton({
  option,
  groups,
  onGroupStackChange,
}: {
  option: ResourceToolbarGroupOption;
  groups: readonly ResourceViewGroup[];
  onGroupStackChange?: (groups: readonly ResourceViewGroup[]) => void;
}): ReactElement {
  const active = groups.some((group) => group.field === option.group.field);
  const granularities = option.granularities ?? DEFAULT_GRANULARITIES;
  const selectedGranularities = new Set(
    groups
      .filter((group) => group.field === option.group.field && group.granularity)
      .map((group) => group.granularity!),
  );

  return (
    <div className={cn("rounded-6", active && "bg-brand-soft")}>
      <PickerButton
        active={active}
        onClick={() => {
          if (!onGroupStackChange) return;
          if (active) {
            onGroupStackChange(
              groups.filter((group) => group.field !== option.group.field),
            );
          } else {
            onGroupStackChange([...groups, option.group]);
          }
        }}
      >
        {option.type === "date" ? (
          <Glyph name="calendar" className="size-3 text-fg-muted" />
        ) : null}
        <span className="min-w-0 flex-1 truncate">{option.label}</span>
      </PickerButton>
      {option.type === "date" ? (
        <div className="flex px-2 pb-1">
          {granularities.map((granularity) => (
            <button
              key={granularity}
              type="button"
              className={cn(
                "h-5 rounded-6 px-1.5 text-2xs font-medium outline-none focus-visible:focus-ring",
                selectedGranularities.has(granularity)
                  ? "bg-brand text-on-brand"
                  : "text-fg-muted hover:bg-sheet",
              )}
              onClick={() => {
                const nextGroup = { ...option.group, granularity };
                const selected = groups.some((group) =>
                  resourceViewGroupsEqual(group, nextGroup));
                onGroupStackChange?.(
                  selected
                    ? groups.filter((group) =>
                      !resourceViewGroupsEqual(group, nextGroup))
                    : [...groups, nextGroup],
                );
              }}
            >
              {titleCase(granularity)}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function PickerDivider(): ReactElement {
  return <div className="my-1 border-t border-border-subtle" />;
}

function PickerMuted({ children }: { children: ReactNode }): ReactElement {
  return <p className={cn(textRoleVariants({ role: "meta" }), "px-2 py-1")}>{children}</p>;
}

export function ResourceViewSwitcher({
  view,
  onViewChange,
  ariaLabel,
  className,
}: ResourceViewSwitcherProps): ReactElement {
  const t = useBaseT();
  return (
    <div
      className={cn("flex items-center gap-1", className)}
      role="group"
      aria-label={ariaLabel ?? t("dataToolbar.viewSwitcher")}
    >
      <Button
        type="button"
        variant="ghost"
        size="iconSm"
        aria-label={t("dataToolbar.listView")}
        aria-pressed={view === "list"}
        active={view === "list"}
        onClick={() => onViewChange?.("list")}
      >
        <Glyph name="list" className="glyph" />
      </Button>
      <Button
        type="button"
        variant="ghost"
        size="iconSm"
        aria-label={t("dataToolbar.boardView")}
        aria-pressed={view === "board"}
        active={view === "board"}
        onClick={() => onViewChange?.("board")}
      >
        <Glyph name="grid-2x2" className="glyph" />
      </Button>
    </div>
  );
}

function groupLabel(group: ResourceViewGroup): string {
  const field = groupFieldLabel(group.field);
  return group.granularity ? `${field} · ${titleCase(group.granularity)}` : field;
}

const DEFAULT_GRANULARITIES: readonly ResourceViewGroupGranularity[] = [
  "year",
  "quarter",
  "month",
  "week",
  "day",
];

const FILTER_OPERATOR_LABEL = {
  exact: "is",
  inList: "is one of",
  isNull: "is empty",
  isNotNull: "is not empty",
  iExact: "is",
  contains: "contains",
  jsonContains: "contains",
  iContains: "contains",
  startsWith: "starts with",
  iStartsWith: "starts with",
  endsWith: "ends with",
  iEndsWith: "ends with",
  gt: ">",
  gte: ">=",
  lt: "<",
  lte: "<=",
} satisfies Record<ResourceToolbarCustomFilterOperator, string>;

const TEXT_FILTER_OPERATORS: readonly ResourceToolbarCustomFilterOperator[] = [
  "contains",
  "iContains",
  "iExact",
  "startsWith",
  "iStartsWith",
  "endsWith",
  "iEndsWith",
  "isNull",
  "isNotNull",
];

const COMPARISON_FILTER_OPERATORS: readonly ResourceToolbarCustomFilterOperator[] = [
  "exact",
  "gt",
  "gte",
  "lt",
  "lte",
  "isNull",
  "isNotNull",
];

const EXACT_FILTER_OPERATORS: readonly ResourceToolbarCustomFilterOperator[] = [
  "exact",
  "isNull",
  "isNotNull",
];

function operatorsForField(
  field: ResourceToolbarFilterField | undefined,
): readonly ResourceToolbarCustomFilterOperator[] {
  if (field?.operators) return field.operators;
  if (
    field?.type === "number" ||
    field?.type === "date" ||
    field?.type === "datetime"
  ) {
    return COMPARISON_FILTER_OPERATORS;
  }
  if (field?.type === "selection" || field?.type === "boolean") {
    return EXACT_FILTER_OPERATORS;
  }
  return TEXT_FILTER_OPERATORS;
}

function defaultOperator(
  field: ResourceToolbarFilterField | undefined,
): ResourceToolbarCustomFilterOperator {
  return operatorsForField(field)[0] ?? "contains";
}

function operatorForField(
  field: ResourceToolbarFilterField | undefined,
  operator: ResourceToolbarCustomFilterOperator,
): ResourceToolbarCustomFilterOperator {
  return operatorsForField(field).includes(operator)
    ? operator
    : defaultOperator(field);
}

function customFilterNeedsValue(
  operator: ResourceToolbarCustomFilterOperator,
): boolean {
  return operator !== "isNull" && operator !== "isNotNull";
}

function filterInputType(field: ResourceToolbarFilterField | undefined): string {
  if (field?.type === "number") return "number";
  if (field?.type === "date") return "date";
  if (field?.type === "datetime") return "datetime-local";
  return "text";
}

function coerceFilterValue(
  field: ResourceToolbarFilterField,
  value: string,
): string | number | boolean | undefined {
  const trimmed = value.trim();
  if (!trimmed) return undefined;
  if (field.type === "number") {
    const number = Number(trimmed);
    return Number.isFinite(number) ? number : undefined;
  }
  if (field.type === "boolean") {
    return ["1", "true", "yes", "on"].includes(trimmed.toLowerCase());
  }
  return trimmed;
}

function labelText(value: ReactNode): string | null {
  if (typeof value === "string") return value;
  if (typeof value === "number") return String(value);
  return null;
}
