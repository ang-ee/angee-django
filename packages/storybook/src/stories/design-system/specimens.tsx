import { useMemo, useState, type ReactNode } from "react";
import * as U from "@angee/ui";
import { GlyphMatrix, ToneMatrix, TypeRoleMatrix } from "./token-sheets";
import { ActionForms, Authentication, Callback, Charts, CollectionTree, Collections, Editors, Forms, Hooks, Previews, Resources, ThemeSurfaces } from "./surface-specimens";

const noop = () => undefined;
const row = { id: "sample", title: "Review the proposal" };

export function Buttons() {
  return <div className="grid gap-4">{U.recipeAxes(U.buttonVariants).variant.map(variant =>
    <div key={variant} className="flex flex-wrap items-center gap-3">
      <code className={U.textRoleVariants({ role: "caption", className: "w-20" })}>{variant}</code>
      {U.recipeAxes(U.buttonVariants).size.map(size => <U.Button key={size} variant={variant} size={size} aria-label={size.startsWith("icon") ? `${variant} ${size}` : undefined}>
        {size.startsWith("icon") ? <U.Glyph name="plus" /> : size}
      </U.Button>)}
    </div>)}</div>;
}
export function Pills() {
  return <div className="grid gap-4">
    {[...U.TONES].sort().map(tone => <div key={tone} className="flex flex-wrap items-center gap-2">
      <code className={U.textRoleVariants({ role: "caption", className: "w-20" })}>{tone}</code>
      {[...U.FILLS].sort().map(variant => <div key={variant} className="flex items-center gap-1">
        <U.Chip tone={tone} variant={variant}>{variant}</U.Chip>
      </div>)}
    </div>)}
    {U.recipeAxes(U.chipVariants).shape.map(shape => <div key={shape} className="flex flex-wrap items-center gap-3">
      {U.recipeAxes(U.chipVariants).size.map(size => <U.Chip key={size} shape={shape} size={size}>{shape} · {size}</U.Chip>)}
    </div>)}
    <div className="flex flex-wrap gap-3">{U.recipeAxes(U.chipVariants).mark.map(mark => <U.Chip key={mark} tone="success" mark={mark}>{mark}</U.Chip>)}</div>
    <div className="flex flex-wrap gap-3"><U.Chip size="sm" tone="muted" pressed>pressed</U.Chip><U.Chip size="sm" tone="muted" pressed={false}>not pressed</U.Chip></div>
    <U.RemovableChip removeLabel="Example" onRemove={noop}>Example</U.RemovableChip>
    <U.ChipList items={[{ id: "one", label: "First" }, { id: "two", label: "Second" }]} />
    <div className="flex flex-wrap gap-3">{[...U.TONES].sort().map(tone => U.recipeAxes(U.countBadgeVariants).size.map(size =>
      <U.CountBadge key={`${tone}-${size}`} tone={tone} size={size} value={42} />))}</div>
  </div>;
}
export function StatusIcons() {
  return <div className="flex flex-wrap items-center gap-4">
    {U.recipeAxes(U.statusIconVariants).tone.map(tone => U.recipeAxes(U.statusIconVariants).size.map(size =>
      <U.StatusIcon key={`${tone}-${size}`} tone={tone} size={size} label={`${tone} ${size}`} />))}
    {[...U.TONES].sort().map(tone => U.recipeAxes(U.statusDotVariants).size.map(size =>
      <U.StatusDot key={`${tone}-${size}`} tone={tone} size={size} label={`${tone} ${size}`} />))}
  </div>;
}
export function Avatars() {
  return <div className="flex flex-wrap items-end gap-4">
    {U.recipeAxes(U.avatarVariants).size.map(size => <U.Avatar key={size} size={size} alt="Alex Morgan" initials="AM" />)}
    <U.AvatarStack><U.Avatar alt="Alex Morgan" initials="AM" /><U.Avatar alt="Sam Rivers" initials="SR" /></U.AvatarStack>
  </div>;
}
export function Alerts() {
  return <div className="grid gap-3">{[...U.FEEDBACK_INTENTS].sort().map(tone =>
    U.recipeAxes(U.alertVariants).format.flatMap(format => [...U.FILLS].sort().map(variant => <U.Alert
      key={`${tone}-${format}-${variant}`} tone={tone} format={format} variant={variant} title={`${tone} · ${format} · ${variant}`}>
      The owner supplies the glyph, tone and anatomy.
    </U.Alert>)))}</div>;
}
export function Tabs() {
  return <div className="grid gap-4">{U.recipeAxes(U.tabsVariants).variant.map(variant =>
    <U.Tabs key={variant} variant={variant} defaultValue="first">
      <U.Tabs.List aria-label={`${variant} tabs`}>
        <U.Tabs.Tab value="first">Timeline <U.Tabs.Count>4</U.Tabs.Count></U.Tabs.Tab>
        <U.Tabs.Tab value="second">Related</U.Tabs.Tab>
        <U.Tabs.Indicator />
      </U.Tabs.List>
      <U.Tabs.Panel value="first"><U.InlineEmpty label={`${variant} panel`} /></U.Tabs.Panel>
      <U.Tabs.Panel value="second"><U.InlineEmpty label="Related panel" /></U.Tabs.Panel>
    </U.Tabs>)}</div>;
}
export function Toolbars() {
  return <div className="grid gap-4">{U.recipeAxes(U.toolbarVariants).surface.map(surface =>
    <U.Toolbar key={surface} surface={surface} aria-label={`${surface} toolbar`}>
      {(["ghost", "secondary", "primary"] as const).map(variant => <U.Toolbar.Group key={variant}>
        {(["sm", "md", "iconSm", "iconMd"] as const).map(size => <U.Toolbar.Button key={size} variant={variant} size={size} aria-label={size.startsWith("icon") ? `${surface} ${variant} ${size}` : undefined}>
          {size.startsWith("icon") ? <U.Glyph name="plus" /> : size}
        </U.Toolbar.Button>)}
      </U.Toolbar.Group>)}<U.Toolbar.Separator /><U.Toolbar.Spacer /><U.SearchInput size="sm" aria-label={`${surface} search`} placeholder={surface} />
    </U.Toolbar>)}</div>;
}
export function PaneRows() {
  const [active, setActive] = useState(false);
  const [folded, setFolded] = useState(false);
  return <div className="grid gap-4">
    <div className="flex gap-2"><U.ScopeChip label="All" count={3} active={!active} onClick={() => setActive(false)} />
      <U.ScopeChip label="Needs you" count={1} active={active} tone="warning" onClick={() => setActive(true)} /></div>
    <U.PaneRows>
      <U.PaneLabel count={3} folded={folded} onToggle={() => setFolded(!folded)}
        action={<U.PaneLabelPopover icon="plus" label="Add"><U.Input aria-label="Name" placeholder="Name" /></U.PaneLabelPopover>}>Documents</U.PaneLabel>
      {!folded && <>
        <li><U.PaneRow title="Review the proposal" detail="Waiting" meta="09:41" sub="Alex Morgan · 3 changes" tone="warning" needsMe onOpen={() => setActive(true)} verb="Open" /></li>
        <li><U.PaneRow title="Linked record" icon="link" detail="Published" href="#record" meta="12 KB" /></li>
        <li><U.PaneRow title="Selected record" selected sub="Read-only pointer" /></li>
      </>}
    </U.PaneRows>
  </div>;
}
export function PaneToolbar() {
  const [search, setSearch] = useState(false);
  return <U.PaneToolbarHost title="Related" back={{ label: "Timeline", onClick: noop }}>
    <U.PaneToolbar
      scope={<U.ScopeChip label="All" active count={3} onClick={noop} />}
      search onSearch={() => setSearch(!search)}
      actions={[{ icon: "plus", label: "Add", onClick: noop }, { icon: "bell", label: "Needs you · 2", badge: 2, onClick: noop }, { icon: "plus", label: "New", text: true, items: [{ label: "Page", icon: "file", onClick: noop }] }]}
      overflow={<U.PaneToolbarMenu items={[{ label: "Refresh", icon: "refresh", onClick: noop }]} />}
    />
    {search && <U.SearchInput aria-label="Search related" placeholder="Search related" />}
    <PaneRows />
  </U.PaneToolbarHost>;
}
export function States() {
  return <div className="grid gap-4">
    <U.EmptyState title="Nothing here yet" description="Add a record to get started." actions={<U.Button>Add record</U.Button>} />
    <U.ErrorBanner title="Unable to load records" description="The service is unavailable." actions={<U.Button>Retry</U.Button>} />
    <U.InlineEmpty label="No matching records" />
    <U.LoadingPanel message="Loading records" />
    <U.LazyBoundary pending={<U.LoadingPanel />}><U.Chip>Loaded content</U.Chip></U.LazyBoundary>
    <U.SkeletonStatus label="Loading" /><U.SkeletonText />
  </div>;
}
export function Facts() {
  return <div className="grid gap-4">
    <dl className="grid gap-4">{U.recipeAxes(U.definitionPairVariants).density.map(density => <U.DefinitionPair key={density} density={density}
      label="Owner" value="Alex Morgan" detail={density} action={<U.Button size="sm">Edit</U.Button>} />)}</dl>
    <U.MetaGrid rows={[["Owner", "Alex Morgan"], ["State", <U.Chip key="state" tone="success">Active</U.Chip>]]} />
    <U.ComparisonRows columns={[{ key: "before", label: "Before" }, { key: "after", label: "After" }]} rows={[{ key: "title", label: "Title", cells: { before: "Draft", after: "Published" }, changed: true }]} />
    <U.RelativeTime value="2026-10-19T12:00:00Z" />
    <U.AbsoluteTime value="2026-10-19T12:00:00Z" display="datetime" />
    <U.ComparisonRows columns={[{ key: "left", label: "Ada" }, { key: "right", label: "Grace" }]}
      rows={[{ key: "notes", label: "Notes", cells: { left: "Met in 2024", right: "Met in 2025" } }]}
      choice={{ value: { notes: "left" }, onChange: noop, label: (row) => `Keep ${String(row.label)}` }} />
    <U.RecordReference model="notes.Note" id="sample" label="Project handbook" />
    <U.RecordReferenceRow model="notes.Note" id="sample" references={[]} detail="Retained reference" />
    <U.RecordPager navigation={{ current: 2, total: 8, onPrev: noop, onNext: noop }} />
  </div>;
}
const recordContainers = U.containersFromChildren(U.FORM_CONTAINERS, {
  "form#chrome": { "library.record-control": { content: <U.Button size="sm">Record control</U.Button> } },
});
export function RecordVerbs() {
  const runtime = U.useAppRuntime();
  const fixtureRuntime = useMemo(() => ({ ...runtime, containers: recordContainers }), [runtime]);
  return <U.AppRuntimeProvider runtime={fixtureRuntime}><U.ControlBand>
    <U.SaveDiscardActions isDirty onDiscard={noop} onSave={noop} />
    <U.RecordActionBar record={row} actions={[{ id: "publish", label: "Publish", primary: true, run: async () => undefined }]} />
    <U.RecordChrome value={{ resource: "notes.Note", canonicalResource: "notes.Note", dataProviderName: "public", recordId: row.id, record: row, formReadOnly: false }} />
    <U.ActionMenu><U.ActionTrigger onClick={noop}>Download</U.ActionTrigger></U.ActionMenu>
  </U.ControlBand></U.AppRuntimeProvider>;
}
const events: U.TimelineRailEntry[] = [{
  key: "created", at: "2026-10-19T12:00:00Z", actor: "Alex Morgan", kind: "change",
  title: "Created the record", detail: "Draft", folded: "2 fields", href: "#record",
}];
const kinds: U.TimelineRailKind[] = [{
  id: "change", label: "Changes", icon: "activity", verb: "Open", target: { page: true }, count: 1,
}];
export function Timeline() {
  const [scope, setScope] = useState("all");
  return <U.PaneToolbarHost><U.TimelineRail entries={events} kinds={kinds} scope={scope} onScope={setScope} needsMe={0} onOpen={noop} /></U.PaneToolbarHost>;
}
export function Steps() {
  return <U.StepList items={[
    { id: "done", state: "done", title: "Read the record", outcome: "Complete" },
    { id: "current", state: "current", title: "Review", detail: "Waiting for a decision" },
    { id: "planned", state: "planned", title: "Publish" },
  ]} />;
}
export function ContextAside() {
  return <U.ChatterProvider defaultTab="timeline">
    <div className="h-96"><U.Chatter tabs={[
      { id: "timeline", label: "Timeline", icon: "activity", count: 1, children: <Timeline /> },
      { id: "steps", label: "Steps", icon: "check", children: <Steps /> },
      { id: "related", label: "Related", icon: "link", children: <U.RelatedPane context={{ pathname: "/", params: {}, view: { kind: "record", type: "notes.Note", sqid: "sample" } }} /> },
    ]} /></div>
  </U.ChatterProvider>;
}
export function Related() {
  return <U.ChatterProvider><U.PaneToolbarHost title="Related"><U.RelatedPane context={{
    pathname: "/", params: {}, view: { kind: "record", type: "notes.Note", sqid: "sample" },
  }} /></U.PaneToolbarHost></U.ChatterProvider>;
}
export function Settings() {
  return <U.SettingsShell maxWidth="1100" gap="6">
    <U.SettingsSection title="Appearance" description="The shared settings composition.">
      <U.SurfacePanel title="Preferences"><U.MetaGrid rows={[["Theme", "Inherited"]]} /></U.SurfacePanel>
    </U.SettingsSection>
    <U.DetailSurface title="Record details"><U.DetailSection title="Facts" rows={[["Owner", "Alex Morgan"]]} /></U.DetailSurface>
  </U.SettingsShell>;
}
export function Statusline() {
  return <U.Statusline><U.StatusSegment icon="check" tone="success">Saved</U.StatusSegment><U.StatuslineSpacer />
    <U.StatusSegment>8 records</U.StatusSegment></U.Statusline>;
}

function DialogActions() {
  const confirm = U.useConfirm();
  const prompt = U.usePrompt();
  const trash = U.useTrashPrompt();
  return <div className="flex flex-wrap gap-3">
    <U.Button onClick={() => void confirm({ title: "Publish this record?", body: "The shared confirmation owner." })}>Confirm</U.Button>
    <U.Button onClick={() => void prompt({ title: "Rename", fields: [{ name: "name", label: "Name" }] })}>Prompt</U.Button>
    <U.Button onClick={() => void trash("Draft")}>Trash prompt</U.Button>
  </div>;
}
export function Dialogs() {
  return <div className="grid gap-4">
    <U.Dialog.Root><U.Dialog.Trigger render={<U.Button>Open dialog</U.Button>} />
      <U.Dialog.Portal><U.Dialog.Backdrop /><U.Dialog.Content>
        <U.Dialog.Header><U.Dialog.Title>Shared dialog</U.Dialog.Title><U.Dialog.Description>Real Base UI parts.</U.Dialog.Description></U.Dialog.Header>
        <U.Dialog.Body><U.Input aria-label="Dialog value" placeholder="Value" /></U.Dialog.Body>
        <U.Dialog.Footer><U.Dialog.Close render={<U.Button>Close</U.Button>} /></U.Dialog.Footer>
      </U.Dialog.Content></U.Dialog.Portal>
    </U.Dialog.Root>
    <DialogActions />
  </div>;
}
export function Controls() {
  const [value,setValue] = useState("first");
  const [checked,setChecked] = useState(false);
  const [page,setPage] = useState(1);
  return <div className="grid gap-4">
    <U.FieldRoot><U.FieldLabel htmlFor="specimen-input">Name</U.FieldLabel><U.Input id="specimen-input" placeholder="Name" />
      <U.FieldDescription>A field with one owner.</U.FieldDescription></U.FieldRoot>
    <U.Checkbox checked={checked} onCheckedChange={setChecked} aria-label="Include archived" />
    <U.Switch checked={checked} onCheckedChange={setChecked} aria-label="Notifications" />
    <U.SwitchRoot checked={checked} onCheckedChange={setChecked} aria-label="Compact switch"><U.SwitchThumb /></U.SwitchRoot>
    <U.SwitchRow label="Email notifications" description="Send a digest" checked={checked} onCheckedChange={setChecked} />
    <U.Select aria-label="Collection" value={value} onValueChange={setValue} options={[{ value: "first", label: "First" }, { value: "second", label: "Second" }]} />
    <U.Tooltip label="A shared tooltip"><U.Button>Hover or focus</U.Button></U.Tooltip>
    <U.Pager page={page} pageSize={10} total={80} onPageChange={setPage} />
    <U.Separator />
    <U.ScrollArea className="h-28"><U.Tree nodes={[{ id: "root", label: "Library", children: [{ id: "child", label: "Handbook" }] }]} /></U.ScrollArea>
    <U.UploadDropTarget onFiles={noop}><U.EmptyState title="Drop a file" description="The upload owner handles drag state." /></U.UploadDropTarget>
    <U.Spinner /><U.Skeleton />
  </div>;
}
export function Displays() {
  return <div className="grid gap-4">
    <U.SectionHeading label="Activity" count={3} hint="Current record" />
    <U.LogStream lines={["Ready", "Reading metadata", "Complete"]} />
    <U.RemovedDisclosure count={1} defaultOpen><U.RemovedItem label="Archived draft" trashedByLabel="Alex Morgan" trashReason="Replaced" onRestore={noop} /></U.RemovedDisclosure>
    <U.PreviewSkeleton label="Loading preview" />
    <U.AppBootSkeleton />
    <U.ThemeLogo size={32} /><U.AgentGlyph size={32} aria-label="Agent" />
  </div>;
}
const drawerContainers = U.containersFromChildren(U.SHELL_CONTAINERS, {
  "shell#drawers-right": { "library.tools": { content: { title: "Tools", icon: "grid", render: () => <U.LogStream lines={["Ready", "Tool output"]} /> } } },
});
function DrawerContents() {
  const drawer = U.useDrawerState();
  return <div className="relative h-72">
    <U.Button onClick={() => drawer.open("right","library.tools")}>Open tools drawer</U.Button>
    <U.DrawerRail edge="right" /><U.DrawerOverlay edge="right" />
  </div>;
}
export function Drawers() {
  const runtime = U.useAppRuntime();
  const fixtureRuntime = useMemo(() => ({ ...runtime, containers: drawerContainers }), [runtime]);
  return <U.AppRuntimeProvider runtime={fixtureRuntime}><U.DrawerProvider><DrawerContents /></U.DrawerProvider></U.AppRuntimeProvider>;
}
export function ShellRegions() {
  return <div className="grid gap-4">
    <U.DropdownMenu.Root><U.DropdownMenu.Trigger render={<U.Button>Developer menu</U.Button>} />
      <U.DropdownMenu.Portal><U.DropdownMenu.Positioner><U.DropdownMenu.Content>
        <U.DeveloperModeMenuItem />
      </U.DropdownMenu.Content></U.DropdownMenu.Positioner></U.DropdownMenu.Portal>
    </U.DropdownMenu.Root><U.DeveloperMenu />
    <div className="h-72"><U.Workbench primary={<PaneRows />} secondary={<Steps />}><U.EmptyState title="Subject" description="The shared workbench owns the panes." /></U.Workbench></div>
  </div>;
}

/** Explicit specimens: fixtures provide props; all displayed anatomy ships in ui. */
export type CatalogExportName = keyof typeof U | keyof typeof import("@angee/app");
export const specimens = {
  FILLS: ToneMatrix, TONES: ToneMatrix, baseIcons: GlyphMatrix, textRoleVariants: TypeRoleMatrix,
  ActionMenu: RecordVerbs, ActionTrigger: RecordVerbs, Alert: Alerts, Avatar: Avatars, AvatarStack: Avatars,
  Button: Buttons, Chatter: ContextAside, Chip: Pills, ChipList: Pills,
  ComparisonRows: Facts, ControlBand: RecordVerbs, CountBadge: Pills, DefinitionPair: Facts,
  DetailSection: Settings, DetailSurface: Settings, EmptyState: States, ErrorBanner: States,
  InlineEmpty: States, LazyBoundary: States, LoadingPanel: States, MetaGrid: Facts,
  PaneLabel: PaneRows, PaneLabelAction: PaneRows, PaneLabelPopover: PaneRows, PaneRow: PaneRows, PaneRows,
  PaneToolbar, PaneToolbarHost: PaneToolbar, PaneToolbarMenu: PaneToolbar,
  RecordActionBar: RecordVerbs, RecordPager: Facts, RecordReference: Facts, RecordReferenceRow: Facts,
  RelatedPane: Related, RelativeTime: Facts, RemovableChip: Pills, SaveDiscardActions: RecordVerbs,
  ScopeChip: PaneRows, SettingsSection: Settings, SettingsShell: Settings, SkeletonStatus: States,
  SkeletonText: States, StatusDot: StatusIcons, StatusIcon: StatusIcons, StatusSegment: Statusline,
  Statusline, StatuslineSpacer: Statusline, StepList: Steps, SurfacePanel: Settings,
  Tabs, TabsCount: Tabs, TabsIndicator: Tabs, TabsList: Tabs, TabsPanel: Tabs, TabsRoot: Tabs, TabsTab: Tabs,
  TimelineRail: Timeline, Toolbar: Toolbars, ToolbarButton: Toolbars,
  ToolbarGroup: Toolbars, ToolbarRoot: Toolbars, ToolbarSeparator: Toolbars, ToolbarSpacer: Toolbars,
  AppBootSkeleton: Displays, Checkbox: Controls, CheckboxVisual: Controls, DeveloperMenu: ShellRegions, DeveloperModeMenuItem: ShellRegions,
  Dialog: Dialogs, DialogBackdrop: Dialogs, DialogBody: Dialogs, DialogClose: Dialogs, DialogContent: Dialogs,
  DialogDescription: Dialogs, DialogFooter: Dialogs, DialogHeader: Dialogs, DialogPortal: Dialogs, DialogRoot: Dialogs,
  DialogTitle: Dialogs, DialogTrigger: Dialogs, DrawerOverlay: Drawers, DrawerRail: Drawers,
  FieldDescription: Controls, FolderTree: Controls, LogStream: Displays, Pager: Controls, PreviewSkeleton: Displays,
  RemovedDisclosure: Displays, RemovedItem: Displays, ScrollArea: Controls, SectionHeading: Displays, Select: Controls,
  Separator: Controls, Skeleton: Controls, Spinner: Controls, Switch: Controls, ThemeLogo: Displays,
  Tooltip: Controls, Tree: Controls, UploadDropTarget: Controls, Workbench: ShellRegions,
  useConfirm: Dialogs, usePrompt: Dialogs, useTrashPrompt: Dialogs,
  Action: Forms, ActionFormDialog: ActionForms, AgentGlyph: Displays, AggregatePanel: Collections,
  BoundDescriptorField: Forms, BoundFormValue: Forms, CollectionTreeView: CollectionTree, Column: Collections, DashboardBars: Charts,
  DashboardDonut: Charts, DashboardView: Charts, DatePopover: Editors, DrawerResourceList: Collections,
  Facet: Collections, Field: Forms, Form: Forms, Group: Forms, ImagePreviewSurface: Previews, InlineTextAction: Editors,
  JsonEditor: Editors, JsonValueView: Editors, List: Collections, Metric: Charts, OAuthCallback: Callback,
  PassthroughChrome: Authentication, PreviewPane: Previews, RecordChrome: RecordVerbs, RecordRailGroup: Forms,
  RemovedRecords: Collections, ResourceCreate: Resources, ResourceEdit: Resources, ResourceShow: Resources, RowsField: Editors,
  SwitchRoot: Controls, SwitchRow: Controls, SwitchThumb: Controls, Tab: Forms,
  ThemeCustomizationEditor: ThemeSurfaces, ThemePreviewFrame: ThemeSurfaces, ThemeSpecimenSurface: ThemeSurfaces,
  UsernamePasswordForm: Authentication, VisibilityControl: Editors,
  useActionOutcomeMutation: Hooks, useActionResultMutation: Hooks, useActionSelectionLimit: Hooks,
  useEnumOptions: Hooks, useFormSpecFields: Hooks, useFormViewValues: Forms,
  useImplCategory: Hooks, useImplChoices: Hooks, useImplConfigFields: Hooks, useImplPrefill: Hooks,
  useListIdentities: Hooks, useRecordAction: Hooks, useRecordActionMutation: Hooks,
  useRecordChromeActionMutation: Hooks, useRecordChromeActionOutcome: Hooks, useRouteRecordId: Hooks,
  useTrashActions: Hooks, useTrashRowActions: Hooks,
} satisfies Partial<Record<CatalogExportName, () => ReactNode>>;
