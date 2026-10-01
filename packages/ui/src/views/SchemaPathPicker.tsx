import { useId, useMemo, useState } from "react";

import { InlineEmpty } from "../fragments/InlineEmpty";
import { useUiT } from "../i18n";
import { cn } from "../lib/cn";
import { FieldLabel, FieldRoot } from "../ui/field";
import { Input } from "../ui/input";
import { Tree, type TreeNode } from "../ui/tree";
import { isJsonObject, type JsonObject, type JsonValue } from "../widgets/json-value";
import { resolveSchemaReference } from "./form/form-spec";

export type SchemaPath = readonly (string | number)[];
export type SchemaPathSchema = JsonObject | boolean;

export interface SchemaPathPickerProps {
  schema: SchemaPathSchema;
  /** null means no selection; [] selects the complete value. */
  value: SchemaPath | null;
  onChange: (path: SchemaPath) => void;
  label?: string;
  disabled?: boolean;
  className?: string;
  /**
   * Override reference resolution for a schema registry. Root-local definitions
   * resolve through the form schema owner by default.
   * Unresolved references remain selectable opaque values. Recursive references
   * stop at their first repeated schema. No remote reads occur in this control.
   */
  resolveReference?: (reference: string) => SchemaPathSchema | undefined;
}

interface PathEntry {
  path: SchemaPath;
  title?: string;
  description?: string;
  unresolved: boolean;
  hasChildren: boolean;
  children: PathEntry[];
}
interface PathInput { path: SchemaPath; raw: string; kind: "index" | "key"; valid: boolean }
const EMPTY_INPUTS: Readonly<Record<string, string>> = {};

/**
 * Pick a concrete data path from declared JSON Schema properties, array items,
 * tuple prefixItems and union/intersection branches. Object keys stay literal
 * segments: dots, slashes and empty keys are never parsed as path syntax.
 * Dynamic object keys and array indices are entered explicitly. Children are
 * built only for expanded nodes and ancestors of the controlled selection.
 * This enumerates declared shapes; the schema owner validates branch constraints.
 */
export function SchemaPathPicker({
  schema, value, onChange, label, disabled = false, className, resolveReference,
}: SchemaPathPickerProps) {
  const t = useUiT();
  const id = useId();
  const selection = JSON.stringify(value);
  const [draft, setDraft] = useState({ selection, inputs: EMPTY_INPUTS });
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(() => new Set(["[]"]));
  // Index exploration is local to the current selection. A new controlled path
  // supplies its own indices without mirroring that path into component state.
  const inputs = draft.selection === selection ? draft.inputs : EMPTY_INPUTS;
  const { entry, controls } = useMemo(
    () => pathTree(schema, JSON.parse(selection) as SchemaPath | null, inputs, expanded, resolveReference),
    [schema, selection, inputs, expanded, resolveReference],
  );
  const toNode = (item: PathEntry): TreeNode => ({
    id: JSON.stringify(item.path),
    label: <span title={item.description}>
      {item.title || (item.path.length ? String(item.path.at(-1)) || t("schemaPath.emptyKey") : t("schemaPath.root"))}
      {item.unresolved ? <span className="ml-2 text-fg-muted">{t("schemaPath.opaque")}</span> : null}
    </span>,
    children: item.children.map(toNode),
    hasChildren: item.hasChildren,
  });
  return <section
    aria-label={label ?? t("schemaPath.label")}
    aria-disabled={disabled || undefined}
    className={cn("grid gap-3", className)}
  >
    {entry ? <Tree
      aria-label={label ?? t("schemaPath.label")}
      nodes={[toNode(entry)]}
      selectedId={value === null ? undefined : JSON.stringify(value)}
      onExpand={(path) => setExpanded((current) => new Set([...current, path]))}
      onSelect={disabled ? undefined : (selected) => {
        const find = (item: PathEntry): PathEntry | undefined =>
          JSON.stringify(item.path) === selected ? item : item.children.map(find).find(Boolean);
        const chosen = find(entry);
        if (chosen) onChange(chosen.path);
      }}
    /> : <InlineEmpty label={t("schemaPath.empty")} />}
    {controls.map(({ path, raw, kind, valid }, index) => {
      const controlId = `${id}-${index}`;
      return <FieldRoot key={`${kind}:${JSON.stringify(path)}`}>
        <FieldLabel htmlFor={controlId}>
          {t(kind === "index" ? "schemaPath.index" : "schemaPath.key", { path: path.length ? JSON.stringify(path) : t("schemaPath.root") })}
        </FieldLabel>
        <Input id={controlId} inputMode={kind === "index" ? "numeric" : "text"} value={raw} disabled={disabled}
          invalid={!valid}
          onChange={(event) => {
            const next = event.currentTarget.value;
            const segment = kind === "index" ? arrayIndex(next) : next;
            const sharesPrefix = path.every((part, offset) => value?.[offset] === part);
            const nextPath = segment === undefined ? null : [...path, segment,
              ...(sharesPrefix ? value?.slice(path.length + 1) ?? [] : [])];
            const nextInputs = { ...inputs, [`${kind}:${JSON.stringify(path)}`]: next };
            const proposed = pathTree(schema, nextPath, nextInputs, expanded, resolveReference);
            let chosen = proposed.entry;
            for (const part of nextPath ?? []) {
              const child = chosen?.children.find((candidate) => candidate.path.at(-1) === part);
              if (!child) break;
              chosen = child;
            }
            const accepted = nextPath && chosen && chosen.path.length > path.length
              && proposed.controls.find((control) => control.kind === kind
                && JSON.stringify(control.path) === JSON.stringify(path))?.valid;
            setDraft((current) => ({
              selection,
              inputs: {
                ...(current.selection === selection ? current.inputs : EMPTY_INPUTS),
                [`${kind}:${JSON.stringify(path)}`]: next,
              },
            }));
            if (accepted && chosen) onChange(chosen.path);
          }}
        />
      </FieldRoot>;
    })}
  </section>;
}

function arrayIndex(raw: string): number | undefined {
  const parsed = /^\d+$/.test(raw) ? Number(raw) : Number.NaN;
  return Number.isSafeInteger(parsed) && parsed >= 0 ? parsed : undefined;
}

function asSchema(value: JsonValue | undefined): SchemaPathSchema | undefined {
  return typeof value === "boolean" || isJsonObject(value) ? value : undefined;
}

function pathTree(
  schema: SchemaPathSchema,
  selected: SchemaPath | null,
  inputs: Readonly<Record<string, string>>,
  expanded: ReadonlySet<string>,
  resolve: SchemaPathPickerProps["resolveReference"],
): { entry?: PathEntry; controls: PathInput[] } {
  const controls: PathInput[] = [];
  const resolveLocal = (reference: string): SchemaPathSchema | undefined => {
    if (typeof schema === "boolean") return undefined;
    try {
      return asSchema(resolveSchemaReference(reference, {
        $defs: isJsonObject(schema.$defs) ? schema.$defs : undefined,
        definitions: isJsonObject(schema.definitions) ? schema.definitions : undefined,
      }));
    } catch { return undefined; }
  };
  function visit(schemas: readonly SchemaPathSchema[], path: SchemaPath, ancestors: ReadonlySet<JsonObject>, references: ReadonlySet<string>): PathEntry | undefined {
    if (schemas.every((item) => item === false)) return undefined;
    const branches: JsonObject[] = [];
    const seen = new Set(ancestors);
    const seenReferences = new Set(references);
    let unresolved = false;
    function expand(item: SchemaPathSchema): void {
      if (typeof item === "boolean" || seen.has(item)) return;
      seen.add(item);
      branches.push(item);
      if (typeof item.$ref === "string" && !seenReferences.has(item.$ref)) {
        seenReferences.add(item.$ref);
        const target = (resolve ?? resolveLocal)(item.$ref);
        if (target === undefined) unresolved = true;
        else expand(target);
      }
      for (const keyword of ["allOf", "anyOf", "oneOf"]) {
        const options = item[keyword];
        if (Array.isArray(options)) for (const option of options) {
          const child = asSchema(option);
          if (child !== undefined) expand(child);
        }
      }
    }
    schemas.forEach(expand);
    const sharesPrefix = path.every((part, index) => selected?.[index] === part);
    const opened = expanded.has(JSON.stringify(path)) || (sharesPrefix && path.length < (selected?.length ?? 0));
    const children = new Map<string | number, SchemaPathSchema[]>();
    function add(key: string | number, candidate: JsonValue | undefined): void {
      const child = asSchema(candidate);
      if (child !== undefined) children.set(key, [...(children.get(key) ?? []), child]);
    }
    for (const branch of branches) {
      if (isJsonObject(branch.properties)) {
        for (const [key, child] of Object.entries(branch.properties)) add(key, child);
      }
    }
    const arrayBranches = branches.filter((branch) => branch.type === "array"
      || (Array.isArray(branch.type) && branch.type.includes("array"))
      || branch.items !== undefined || Array.isArray(branch.prefixItems));
    if (arrayBranches.length) {
      const selectedIndex = selected?.[path.length];
      const raw = inputs[`index:${JSON.stringify(path)}`] ?? String(sharesPrefix && typeof selectedIndex === "number" ? selectedIndex : 0);
      const index = arrayIndex(raw);
      if (index !== undefined) for (const branch of arrayBranches) {
        const prefix = branch.prefixItems;
        const legacy = branch.items;
        add(index, Array.isArray(prefix) && index < prefix.length ? prefix[index]
          : Array.isArray(legacy) ? legacy[index] ?? branch.additionalItems ?? true : legacy ?? true);
      }
      if (opened) controls.push({ path, raw, kind: "index",
        valid: index !== undefined && Boolean(children.get(index)?.some((candidate) => candidate !== false)),
      });
    }
    const dynamic = branches.filter((branch) => isJsonObject(branch.patternProperties)
      || branch.additionalProperties === true || isJsonObject(branch.additionalProperties)
      || (branch.additionalProperties !== false && (branch.type === "object"
        || (Array.isArray(branch.type) && branch.type.includes("object")) || isJsonObject(branch.properties))));
    if (dynamic.length) {
      const selectedKey = selected?.[path.length];
      const raw = inputs[`key:${JSON.stringify(path)}`]
        ?? (sharesPrefix && typeof selectedKey === "string" ? selectedKey : "");
      const candidates = dynamic.flatMap((branch) => {
        const patterns = isJsonObject(branch.patternProperties) ? Object.entries(branch.patternProperties) : [];
        const matching = patterns.filter(([pattern]) => {
          try { return new RegExp(pattern, "u").test(raw); } catch { return false; }
        });
        if (isJsonObject(branch.properties) && Object.hasOwn(branch.properties, raw)) {
          return [branch.properties[raw], ...matching.map(([, candidate]) => candidate)];
        }
        return matching.length ? matching.map(([, candidate]) => candidate)
          : [branch.additionalProperties ?? true];
      });
      const valid = candidates.some((candidate) => candidate !== false && asSchema(candidate) !== undefined);
      if (opened) controls.push({ path, raw, kind: "key", valid });
      if (valid && (inputs[`key:${JSON.stringify(path)}`] !== undefined || (sharesPrefix && typeof selectedKey === "string"))) {
        for (const candidate of candidates) add(raw, candidate);
      }
    }
    const titled = branches.find((branch) => typeof branch.title === "string");
    const described = branches.find((branch) => typeof branch.description === "string");
    return {
      path, unresolved, hasChildren: children.size > 0 || dynamic.length > 0,
      title: typeof titled?.title === "string" ? titled.title : undefined,
      description: typeof described?.description === "string" ? described.description : undefined,
      children: opened ? [...children].flatMap(([key, candidates]) => {
        const child = visit(candidates, [...path, key], seen, seenReferences);
        return child ? [child] : [];
      }) : [],
    };
  }
  return { entry: visit([schema], [], new Set(), new Set()), controls };
}
