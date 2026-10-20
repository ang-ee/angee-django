import type { Meta, StoryObj } from "@storybook/react-vite";
import * as U from "@angee/ui";
import { OAuthCallback, UsernamePasswordForm } from "@angee/app";
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
export const ActionForms: Story = { args: { owner: "ActionFormDialog" }, parameters: { owner: U.ActionFormDialog } };
export const Alerts: Story = { args: { owner: "Alert" }, parameters: { owner: U.Alert } };
export const Authentication: Story = { args: { owner: "UsernamePasswordForm" }, parameters: { owner: UsernamePasswordForm } };
export const Avatars: Story = { args: { owner: "Avatar" }, parameters: { owner: U.Avatar } };
export const Buttons: Story = { args: { owner: "Button" }, parameters: { owner: U.Button } };
export const Callback: Story = { args: { owner: "OAuthCallback" }, parameters: { owner: OAuthCallback } };
export const Charts: Story = { args: { owner: "DashboardView" }, parameters: { owner: U.DashboardView } };
export const CollectionTree: Story = { args: { owner: "CollectionTreeView" }, parameters: { owner: U.CollectionTreeView } };
export const Collections: Story = { args: { owner: "List" }, parameters: { owner: U.List } };
export const ContextAside: Story = { args: { owner: "Chatter" }, parameters: { owner: U.Chatter } };
export const Controls: Story = { args: { owner: "Select" }, parameters: { owner: U.Select } };
export const Dialogs: Story = { args: { owner: "Dialog" }, parameters: { owner: U.Dialog } };
export const Displays: Story = { args: { owner: "LogStream" }, parameters: { owner: U.LogStream } };
export const Drawers: Story = { args: { owner: "DrawerRail" }, parameters: { owner: U.DrawerRail } };
export const Editors: Story = { args: { owner: "JsonEditor" }, parameters: { owner: U.JsonEditor } };
export const Facts: Story = { args: { owner: "DefinitionPair" }, parameters: { owner: U.DefinitionPair } };
export const Forms: Story = { args: { owner: "Form" }, parameters: { owner: U.Form } };
export const Hooks: Story = { args: { owner: "useRecordAction" }, parameters: { owner: U.useRecordAction } };
export const PaneRows: Story = { args: { owner: "PaneRow" }, parameters: { owner: U.PaneRow } };
export const PaneToolbar: Story = { args: { owner: "PaneToolbar" }, parameters: { owner: U.PaneToolbar } };
export const Pills: Story = { args: { owner: "Chip" }, parameters: { owner: U.Chip } };
export const Previews: Story = { args: { owner: "PreviewPane" }, parameters: { owner: U.PreviewPane } };
export const RecordVerbs: Story = { args: { owner: "RecordActionBar" }, parameters: { owner: U.RecordActionBar } };
export const Related: Story = { args: { owner: "RelatedPane" }, parameters: { owner: U.RelatedPane } };
export const Resources: Story = { args: { owner: "ResourceCreate" }, parameters: { owner: U.ResourceCreate } };
export const Settings: Story = { args: { owner: "SettingsShell" }, parameters: { owner: U.SettingsShell } };
export const ShellRegions: Story = { args: { owner: "Workbench" }, parameters: { owner: U.Workbench } };
export const States: Story = { args: { owner: "EmptyState" }, parameters: { owner: U.EmptyState } };
export const StatusIcons: Story = { args: { owner: "StatusIcon" }, parameters: { owner: U.StatusIcon } };
export const Statusline: Story = { args: { owner: "Statusline" }, parameters: { owner: U.Statusline } };
export const Steps: Story = { args: { owner: "StepList" }, parameters: { owner: U.StepList } };
export const Tabs: Story = { args: { owner: "Tabs" }, parameters: { owner: U.Tabs } };
export const ThemeSurfaces: Story = { args: { owner: "ThemePreviewFrame" }, parameters: { owner: U.ThemePreviewFrame } };
export const Timeline: Story = { args: { owner: "TimelineRail" }, parameters: { owner: U.TimelineRail } };
export const TimelineEntry: Story = { args: { owner: "TimelineEntry" }, parameters: { owner: U.TimelineEntry } };
export const Toolbars: Story = { args: { owner: "Toolbar" }, parameters: { owner: U.Toolbar } };
