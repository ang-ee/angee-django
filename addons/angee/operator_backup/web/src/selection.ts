import * as v from "valibot";
const group = { mode: v.picklist(["all", "selected", "none"]), names: v.array(v.string()), exclude: v.array(v.string()) };
/** Parse the stored JSON boundary; authoritative selection validation belongs to BackupPlan. */
const GroupSchema = v.strictObject(group);
const SourcesSchema = v.strictObject({ ...group, policy: v.picklist(["bundle", "fetch_published"]) });
export const BackupSelectionSchema = v.strictObject({ version: v.literal(1), databases: GroupSchema, service_state: GroupSchema, workspaces: GroupSchema, sources: SourcesSchema, storage: GroupSchema });
export type BackupSelection = v.InferOutput<typeof BackupSelectionSchema>;
export const BACKUP_GROUPS = ["databases", "service_state", "workspaces", "sources", "storage"] as const;
export function defaultBackupSelection(): BackupSelection {
  const group = () => ({ mode: "all" as const, names: [], exclude: [] });
  return { version: 1, databases: group(), service_state: group(), workspaces: group(), sources: { ...group(), policy: "bundle" }, storage: group() };
}

/** Commit a group control change atomically, dropping inactive names and letting the edited list win overlaps. */
export function updateSelectionGroup(selection: BackupSelection, key: typeof BACKUP_GROUPS[number], value: unknown): BackupSelection {
  const parsed = v.safeParse(key === "sources" ? SourcesSchema : GroupSchema, value);
  if (!parsed.success) return selection;
  const previous = selection[key];
  const group = parsed.output;
  const namesChanged = JSON.stringify(previous.names) !== JSON.stringify(group.names);
  const names = group.mode === "selected" ? [...new Set(group.names)] : [];
  const exclude = [...new Set(group.exclude)];
  const changed = { ...group,
    names: namesChanged ? names : names.filter((name) => !exclude.includes(name)),
    exclude: namesChanged ? exclude.filter((name) => !names.includes(name)) : exclude,
  };
  return key === "sources"
    ? { ...selection, sources: { ...changed, policy: "policy" in group ? group.policy : selection.sources.policy } }
    : { ...selection, [key]: changed };
}

/** Native inventory checks edit the form draft; they do not create a second persisted selection. */
export function applyInventorySelection(selection: BackupSelection, resources: readonly { group: string; name: string }[], action: "include" | "exclude"): BackupSelection {
  let next = selection;
  for (const key of BACKUP_GROUPS) {
    const names = resources.filter((resource) => resource.group === key).map((resource) => resource.name);
    if (!names.length) continue;
    const current = next[key];
    next = updateSelectionGroup(next, key, action === "include"
      ? { ...current, mode: current.mode === "none" ? "selected" : current.mode,
        names: current.mode === "all" ? [] : [...current.names, ...names], exclude: current.exclude.filter((name) => !names.includes(name)) }
      : { ...current, names: current.names.filter((name) => !names.includes(name)), exclude: [...current.exclude, ...names] });
  }
  return next;
}

/** Accept only a validated operator snapshot of the current review draft. */
export function matchingPreviewSelection(snapshot: unknown, selection: BackupSelection): BackupSelection | null {
  const parsed = v.safeParse(BackupSelectionSchema, snapshot);
  return parsed.success && JSON.stringify(parsed.output) === JSON.stringify(selection) ? parsed.output : null;
}
