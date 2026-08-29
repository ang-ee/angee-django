import * as React from "react";
import { type Row } from "@angee/metadata";
import { Glyph } from "../../../chrome/Glyph";
import { ResourceViewSwitcher } from "../../../toolbars";
import { ControlBandProvider } from "../../../layouts/ControlBand";
import { type ListColumn, type ListViewProps, type ResourceListSnapshot } from "../ListView";
import type { ListComponent } from "../List";
import { type ListViewNavigationScope } from "../resource-view-surface";
import { type ResourceViewKind } from "../resource-view-model";
import { type ActionDescriptor } from "../../page";
import { RecordPager, type RecordNavigation } from "../RecordPager";
import type { RecordSmartButtonDescriptor } from "./public";
export function ListStateProbe<TRow extends Row>({
  list: ListComponent,
  resource,
  columns,
  listRenderProps,
  navigationScope,
  onListStateChange,
}: {
  list: ListComponent<TRow>;
  resource: string;
  columns: readonly ListColumn<TRow>[];
  listRenderProps: Partial<ListViewProps<TRow>>;
  navigationScope: ListViewNavigationScope | null;
  onListStateChange: (state: ResourceListSnapshot<TRow>) => void;
}): React.ReactElement {
  const content = (
    <ListComponent
      resource={resource}
      columns={columns}
      {...listRenderProps}
      {...(navigationScope ? { navigationScope } : {})}
      onListStateChange={onListStateChange}
    />
  );
  return (
    // The probe exists only to keep the list's state callbacks alive off-screen;
    // clearing the band host keeps its ControlBand from portaling OUT of this
    // hidden subtree into the page chrome (a portal ignores a hidden ancestor),
    // which stacked a phantom filter/group bar above every open record.
    <ControlBandProvider host={undefined}>
      <div hidden aria-hidden="true">
        {content}
      </div>
    </ControlBandProvider>
  );
}

export const EMPTY_RECORD_ID_SET: ReadonlySet<string> = new Set();
export const EMPTY_ACTIONS: readonly ActionDescriptor[] = [];

export function RecordHeaderActions({
  view,
  navigation,
  smartButtons,
  onViewChange,
}: {
  view: ResourceViewKind;
  navigation: RecordNavigation | null;
  smartButtons: readonly RecordSmartButtonDescriptor[];
  onViewChange: (view: ResourceViewKind) => void;
}): React.ReactElement {
  return (
    <>
      <RecordSmartButtons buttons={smartButtons} />
      {navigation ? <RecordPager navigation={navigation} /> : null}
      <ResourceViewSwitcher
        view={view}
        ariaLabel="Record view switcher"
        onViewChange={onViewChange}
      />
    </>
  );
}

function RecordSmartButtons({
  buttons,
}: {
  buttons: readonly RecordSmartButtonDescriptor[];
}): React.ReactElement | null {
  if (buttons.length === 0) return null;
  return (
    <div className="inline-flex h-btn-md items-stretch gap-px overflow-hidden rounded-6 border border-border-subtle bg-border-subtle">
      {buttons.map((button) => (
        <button
          key={button.id}
          type="button"
          disabled={button.disabled}
          className="inline-flex items-center gap-1.5 bg-sheet px-3 text-xs leading-none text-fg outline-none transition-colors hover:bg-sheet-2 focus-visible:focus-ring disabled:cursor-not-allowed disabled:opacity-60 [&_.glyph]:size-[13px] [&_.glyph]:text-brand"
          onClick={button.onClick}
        >
          <span className="inline-flex items-center gap-1 font-semibold leading-none">
            {button.icon ? <Glyph name={button.icon} /> : null}
            {button.count}
          </span>
          <span className="whitespace-nowrap font-medium text-fg-muted">
            {button.label}
          </span>
        </button>
      ))}
    </div>
  );
}
