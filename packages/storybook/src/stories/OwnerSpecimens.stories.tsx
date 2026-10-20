import type { Meta, StoryObj } from "@storybook/react-vite";
import * as U from "@angee/ui";
import { OAuthCallback, PassthroughChrome, UsernamePasswordForm } from "@angee/app";
import * as Samples from "./design-system/specimens";

function Specimen({ owner }: { owner: keyof typeof Samples.specimens }) {
  const Render = Samples.specimens[owner];
  return <U.ModalsHost><Render /></U.ModalsHost>;
}
const meta = {
  title: "Library/Owners", component: Specimen, parameters: { layout: "fullscreen" },
} satisfies Meta<typeof Specimen>;
export default meta;
type Story = StoryObj<typeof meta>;
export const ActionForms: Story = { args: { owner: "ActionFormDialog" }, parameters: { owner: [U.ActionFormDialog] } };
export const Alerts: Story = { args: { owner: "Alert" }, parameters: { owner: [U.Alert] } };
export const Authentication: Story = { args: { owner: "UsernamePasswordForm" }, parameters: { owner: [PassthroughChrome, UsernamePasswordForm] } };
export const Avatars: Story = { args: { owner: "Avatar" }, parameters: { owner: [U.Avatar, U.AvatarStack] } };
export const Buttons: Story = { args: { owner: "Button" }, parameters: { owner: [U.Button] } };
export const Callback: Story = { args: { owner: "OAuthCallback" }, parameters: { owner: [OAuthCallback] } };
export const Charts: Story = { args: { owner: "DashboardView" }, parameters: { owner: [U.DashboardBars, U.DashboardDonut, U.DashboardView, U.Metric] } };
export const CollectionTree: Story = { args: { owner: "CollectionTreeView" }, parameters: { owner: [U.CollectionTreeView] } };
export const Collections: Story = { args: { owner: "List" }, parameters: { owner: [U.AggregatePanel, U.Column, U.DrawerResourceList, U.Facet, U.List, U.RemovedRecords] } };
export const ContextAside: Story = { args: { owner: "Chatter" }, parameters: { owner: [U.Chatter] } };
export const Controls: Story = { args: { owner: "Select" }, parameters: { owner: [U.Checkbox, U.CheckboxVisual, U.FieldDescription, U.FolderTree, U.Pager, U.ScrollArea, U.Select, U.Separator, U.Skeleton, U.Spinner, U.Switch, U.Tooltip, U.Tree, U.UploadDropTarget, U.SwitchRoot, U.SwitchRow, U.SwitchThumb] } };
export const Dialogs: Story = { args: { owner: "Dialog" }, parameters: { owner: [U.Dialog, U.DialogBackdrop, U.DialogBody, U.DialogClose, U.DialogContent, U.DialogDescription, U.DialogFooter, U.DialogHeader, U.DialogPortal, U.DialogRoot, U.DialogTitle, U.DialogTrigger, U.useConfirm, U.usePrompt, U.useTrashPrompt] } };
export const Displays: Story = { args: { owner: "LogStream" }, parameters: { owner: [U.AppBootSkeleton, U.LogStream, U.PreviewSkeleton, U.RemovedDisclosure, U.RemovedItem, U.SectionHeading, U.ThemeLogo, U.AgentGlyph] } };
export const Drawers: Story = { args: { owner: "DrawerRail" }, parameters: { owner: [U.DrawerOverlay, U.DrawerRail] } };
export const Editors: Story = { args: { owner: "JsonEditor" }, parameters: { owner: [U.DatePopover, U.InlineTextAction, U.JsonEditor, U.JsonValueView, U.RowsField, U.VisibilityControl] } };
export const Facts: Story = { args: { owner: "DefinitionPair" }, parameters: { owner: [U.ComparisonRows, U.DefinitionPair, U.MetaGrid, U.RecordPager, U.RecordReference, U.RecordReferenceRow, U.RelativeTime] } };
export const Forms: Story = { args: { owner: "Form" }, parameters: { owner: [U.Action, U.BoundDescriptorField, U.BoundFormValue, U.Field, U.Form, U.Group, U.RecordRailGroup, U.Tab, U.useFormViewValues] } };
export const Hooks: Story = { args: { owner: "useRecordAction" }, parameters: { owner: [U.useActionOutcomeMutation, U.useActionResultMutation, U.useActionSelectionLimit, U.useEnumOptions, U.useFormSpecFields, U.useImplCategory, U.useImplChoices, U.useImplConfigFields, U.useImplPrefill, U.useListIdentities, U.useRecordAction, U.useRecordActionMutation, U.useRecordChromeActionMutation, U.useRecordChromeActionOutcome, U.useRouteRecordId, U.useTrashActions, U.useTrashRowActions] } };
export const PaneRows: Story = { args: { owner: "PaneRow" }, parameters: { owner: [U.PaneLabel, U.PaneLabelAction, U.PaneLabelPopover, U.PaneRow, U.PaneRows, U.ScopeChip] } };
export const PaneToolbar: Story = { args: { owner: "PaneToolbar" }, parameters: { owner: [U.PaneToolbar, U.PaneToolbarHost, U.PaneToolbarMenu] } };
export const Pills: Story = { args: { owner: "Chip" }, parameters: { owner: [U.Chip, U.ChipList, U.CountBadge, U.RemovableChip] } };
export const Previews: Story = { args: { owner: "PreviewPane" }, parameters: { owner: [U.ImagePreviewSurface, U.PreviewPane] } };
export const RecordVerbs: Story = { args: { owner: "RecordActionBar" }, parameters: { owner: [U.ActionMenu, U.ActionTrigger, U.ControlBand, U.RecordActionBar, U.SaveDiscardActions, U.RecordChrome] } };
export const Related: Story = { args: { owner: "RelatedPane" }, parameters: { owner: [U.RelatedPane] } };
export const Resources: Story = { args: { owner: "ResourceCreate" }, parameters: { owner: [U.ResourceCreate, U.ResourceEdit, U.ResourceShow] } };
export const Settings: Story = { args: { owner: "SettingsShell" }, parameters: { owner: [U.DetailSection, U.DetailSurface, U.SettingsSection, U.SettingsShell, U.SurfacePanel] } };
export const ShellRegions: Story = { args: { owner: "Workbench" }, parameters: { owner: [U.DeveloperMenu, U.DeveloperModeMenuItem, U.Workbench] } };
export const States: Story = { args: { owner: "EmptyState" }, parameters: { owner: [U.EmptyState, U.ErrorBanner, U.InlineEmpty, U.LazyBoundary, U.LoadingPanel, U.SkeletonStatus, U.SkeletonText] } };
export const StatusIcons: Story = { args: { owner: "StatusIcon" }, parameters: { owner: [U.StatusDot, U.StatusIcon] } };
export const Statusline: Story = { args: { owner: "Statusline" }, parameters: { owner: [U.StatusSegment, U.Statusline, U.StatuslineSpacer] } };
export const Steps: Story = { args: { owner: "StepList" }, parameters: { owner: [U.StepList] } };
export const Tabs: Story = { args: { owner: "Tabs" }, parameters: { owner: [U.Tabs, U.TabsCount, U.TabsIndicator, U.TabsList, U.TabsPanel, U.TabsRoot, U.TabsTab] } };
export const ThemeSurfaces: Story = { args: { owner: "ThemePreviewFrame" }, parameters: { owner: [U.ThemeCustomizationEditor, U.ThemePreviewFrame, U.ThemeSpecimenSurface] } };
export const Timeline: Story = { args: { owner: "TimelineRail" }, parameters: { owner: [U.TimelineRail] } };
export const Toolbars: Story = { args: { owner: "Toolbar" }, parameters: { owner: [U.Toolbar, U.ToolbarButton, U.ToolbarGroup, U.ToolbarRoot, U.ToolbarSeparator, U.ToolbarSpacer] } };
