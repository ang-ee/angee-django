import {
  containerName,
  type ComposedContainerChild,
  type ComposedContainers,
  type ContainerAlteration,
  type ContainerChild,
  type ContainerCondition,
  type ContainerRule,
  type ContainersDeclaration,
  type CoreContainer,
} from "@angee/ui/runtime";

import { DEPLOYMENT_LAYER_ID, assertMayAlter, layerAncestry, overridesField, type Layer } from "./layers";

export interface ContainerLayer extends Layer {
  containers?: ContainersDeclaration;
}

const FRAMEWORK = "framework";
const ENTRY_KEYS: ReadonlySet<string> = new Set(["only", "except", "when", "unique"]);
const CHILD_KEYS: ReadonlySet<string> = new Set(["content", "sequence", "before", "after", "permission", "requiredFields", "variant", "key"]);
const ALTERATION_KEYS: ReadonlySet<string> = new Set(["sequence", "before", "after", "remove", "hide"]);
const CONDITION_KEYS: ReadonlySet<string> = new Set(["app", "route", "perspective"]);

type Entry = Record<string, unknown> & { only?: readonly string[]; except?: readonly string[]; when?: ContainerCondition; unique?: "key" };

interface Child extends ComposedContainerChild {
  setBy: Record<string, string>;
}

/**
 * Compile the composed addons' containers.
 *
 * The framework's containers are declared up front; an addon declares a
 * container on its own node by naming its address. A key in the addon's own
 * namespace declares a child; any other key alters a child of an addon it
 * depends on, and two unrelated layers setting one field fail. `only`,
 * `except` and `hide` are render-time narrowing kept per layer, with their
 * `when`. The deployment layer depends on every addon and applies last.
 */
export function compileContainers(
  layers: readonly ContainerLayer[],
  core: readonly CoreContainer[],
  {
    ancestors = layerAncestry(layers),
    canonicalizeModel = (model: string) => model,
  }: {
    ancestors?: ReadonlyMap<string, ReadonlySet<string>>;
    canonicalizeModel?: (spelling: string) => string;
  } = {},
): ComposedContainers {
  const declared: Record<string, { owner: string; models: boolean; unique?: "key" }> = {};
  const modelKinds = new Map<string, string>();
  for (const container of core) {
    if (declared[container.address]) throw new Error(`Container "${container.address}" is declared twice.`);
    declared[container.address] = { owner: FRAMEWORK, models: container.models === true };
    if (container.models) {
      const name = containerName(container.address);
      if (modelKinds.has(name)) throw new Error(`Container name "#${name}" belongs to two kinds.`);
      modelKinds.set(name, container.address);
    }
  }
  const owns = (layer: string, id: string): boolean =>
    layer !== DEPLOYMENT_LAYER_ID && (id === layer || id.startsWith(`${layer}.`));
  const entriesOf = (layer: ContainerLayer): [string, Entry][] =>
    Object.entries(layer.containers ?? {}).flatMap(([address, value]) => {
      const list = Array.isArray(value) ? value : [value];
      return list.map((entry) => {
        if (typeof entry !== "object" || entry === null || Array.isArray(entry)) {
          throw new Error(`Container entry "${address}" of "${layer.id}" must be a mapping.`);
        }
        return [address, entry as Entry] as [string, Entry];
      });
    });

  // A model address resolves through the kind owning its name; anything else is an addon's own container.
  const resolveAddress = (layer: string, address: string): string => {
    const name = containerName(address);
    const node = address.slice(0, address.length - name.length - 1);
    const kind = modelKinds.get(name);
    if (kind) return node === kind.slice(0, kind.indexOf("#")) ? kind : `${canonicalizeModel(node)}#${name}`;
    if (!declared[address]) throw new Error(`Addon "${layer}" names unknown container "${address}".`);
    return address;
  };
  const familyOf = (address: string): string => modelKinds.get(containerName(address)) ?? address;

  // Addons declare their own containers first, so dependents may contribute in any order.
  for (const layer of layers) {
    for (const [address, entry] of entriesOf(layer)) {
      const name = containerName(address);
      const node = address.slice(0, address.length - name.length - 1);
      if (modelKinds.has(name) || !owns(layer.id, node)) continue;
      const known = declared[address];
      if (known && known.owner !== layer.id) throw new Error(`Container "${address}" is declared by "${known.owner}" and "${layer.id}".`);
      declared[address] = { owner: layer.id, models: false, ...(entry.unique ? { unique: entry.unique } : known?.unique ? { unique: known.unique } : {}) };
    }
  }

  const children = new Map<string, Child>();
  const rules = new Map<string, ContainerRule[]>();
  const removed: ComposedContainers["removed"][number][] = [];
  const pending: { layer: string; address: string; id: string; alteration: ContainerAlteration; when?: ContainerCondition }[] = [];
  const pendingRules: { layer: string; address: string; entry: Entry }[] = [];
  const childKey = (address: string, id: string): string => `${address}/${id}`;

  for (const layer of layers) {
    for (const [rawAddress, entry] of entriesOf(layer)) {
      const address = resolveAddress(layer.id, rawAddress);
      validateEntry(layer.id, rawAddress, entry);
      if (entry.unique !== undefined && declared[address]?.owner !== layer.id) {
        throw new Error(`Addon "${layer.id}" sets unique on container "${rawAddress}" it does not own.`);
      }
      if (entry.only || entry.except) pendingRules.push({ layer: layer.id, address, entry });
      for (const [id, value] of Object.entries(entry)) {
        if (ENTRY_KEYS.has(id)) continue;
        const declaresChild = "content" in (value as object);
        if (declaresChild && !owns(layer.id, id)) {
          throw new Error(`Addon "${layer.id}" declares child "${id}" of "${rawAddress}" outside its namespace ("${layer.id}.…").`);
        }
        if (declaresChild) {
          if (entry.when) throw new Error(`Container entry "${rawAddress}" of "${layer.id}" declares child "${id}" under a condition; declare it unconditionally and narrow with only or hide.`);
          const key = childKey(address, id);
          if (children.has(key)) throw new Error(`Addon "${layer.id}" redefines child "${id}" of container "${rawAddress}".`);
          const child = value as ContainerChild;
          for (const field of Object.keys(child)) {
            if (!CHILD_KEYS.has(field)) throw new Error(`Child "${id}" of "${rawAddress}" in "${layer.id}" has unknown key "${field}".`);
          }
          children.set(key, {
            ...child, id, owner: layer.id, address,
            setBy: Object.fromEntries(Object.keys(child).map((field) => [field, layer.id])),
          });
        } else {
          const alteration = value as ContainerAlteration;
          for (const field of Object.keys(alteration)) {
            if (!ALTERATION_KEYS.has(field)) throw new Error(`Alteration of "${id}" on "${rawAddress}" in "${layer.id}" has unknown key "${field}".`);
          }
          if (entry.when && (alteration.sequence !== undefined || alteration.before !== undefined || alteration.after !== undefined || alteration.remove)) {
            throw new Error(`Container entry "${rawAddress}" of "${layer.id}" moves or removes "${id}" under a condition; only render verbs take a condition.`);
          }
          pending.push({ layer: layer.id, address, id, alteration, ...(entry.when ? { when: entry.when } : {}) });
        }
      }
    }
  }

  // Children are found at their own address or, for a model-level alteration, anywhere in the family.
  const family = (address: string): Child[] =>
    [...children.values()].filter((child) => familyOf(child.address) === familyOf(address));
  const findChild = (layer: string, address: string, id: string): Child => {
    const exact = children.get(childKey(address, id));
    if (exact) return exact;
    const found = family(address).filter((child) => child.id === id);
    if (found.length !== 1) throw new Error(`Addon "${layer}" alters ${found.length ? "ambiguous" : "unknown"} child "${id}" of container "${address}".`);
    return found[0]!;
  };

  const addRule = (address: string, rule: ContainerRule): void => {
    rules.set(address, [...(rules.get(address) ?? []), rule]);
  };
  const dependentsOf = (layer: string): string[] =>
    layers.filter((other) => ancestors.get(other.id)?.has(layer)).map((other) => other.id);

  for (const { layer, address, id, alteration, when } of pending) {
    const child = findChild(layer, address, id);
    assertMayAlter(ancestors, layer, child.owner, `child "${id}" of container "${address}"`);
    for (const field of ["sequence", "before", "after"] as const) {
      if (alteration[field] === undefined) continue;
      if (child.address !== address) throw new Error(`Addon "${layer}" positions child "${id}" at "${address}"; position it at "${child.address}".`);
      if (overridesField(ancestors, child.setBy[field], layer, `child "${id}" of "${address}" ${field}`)) {
        (child as unknown as Record<string, unknown>)[field] = alteration[field];
        child.setBy[field] = layer;
      }
    }
    if (alteration.remove) {
      children.delete(childKey(child.address, id));
      removed.push({ address: child.address, id, by: layer });
    }
    if (alteration.hide !== undefined) {
      addRule(address, { layer, ...(when ? { when } : {}), ...(alteration.hide ? { hide: [id] } : { show: [id] }), exempt: [] });
    }
  }

  for (const { layer, address, entry } of pendingRules) {
    const owner = declared[familyOf(address)]?.owner ?? FRAMEWORK;
    if (owner !== FRAMEWORK) assertMayAlter(ancestors, layer, owner, `container "${address}"`);
    const ids = new Set(family(address).flatMap((child) => [child.id, ...(child.variant ? [child.variant.of] : [])]));
    for (const id of [...(entry.only ?? []), ...(entry.except ?? [])]) {
      if (!ids.has(id)) throw new Error(`Addon "${layer}" narrows container "${address}" to unknown child "${id}".`);
    }
    addRule(address, {
      layer,
      ...(entry.when ? { when: entry.when } : {}),
      ...(entry.only ? { only: entry.only } : {}),
      ...(entry.except ? { except: entry.except } : {}),
      exempt: dependentsOf(layer),
    });
  }

  // Variants, positions and unique keys are checked against the final inventory.
  const composed: Record<string, ComposedContainerChild[]> = {};
  for (const child of children.values()) {
    const ids = new Set(family(child.address).map((sibling) => sibling.id));
    if (child.variant && !ids.has(child.variant.of)) throw new Error(`Child "${child.id}" of "${child.address}" is a variant of unknown child "${child.variant.of}".`);
    const { setBy: _setBy, ...plain } = child;
    composed[child.address] = [...(composed[child.address] ?? []), plain];
  }
  for (const address of Object.keys(composed)) {
    const list = composed[address]!;
    const variantKeys = new Map<string, string>();
    for (const child of list) {
      if (!child.variant) continue;
      const key = `${child.variant.of}\0${child.variant.impl}`;
      if (variantKeys.has(key)) throw new Error(`Children "${variantKeys.get(key)}" and "${child.id}" of "${address}" are both variants of "${child.variant.of}" for impl "${child.variant.impl}".`);
      variantKeys.set(key, child.id);
    }
    if (declared[familyOf(address)]?.unique === "key") {
      const keys = new Map<string, string>();
      for (const child of list) {
        if (child.key === undefined) throw new Error(`Child "${child.id}" of "${address}" needs a key: the container renders one child per key.`);
        if (keys.has(child.key)) throw new Error(`Children "${keys.get(child.key)}" and "${child.id}" of "${address}" share key "${child.key}".`);
        keys.set(child.key, child.id);
      }
    }
  }

  return {
    declared,
    children: composed,
    rules: Object.fromEntries(rules),
    removed,
    provenance: Object.fromEntries([...children.values()].map((child) => [childKey(child.address, child.id), { ...child.setBy }])),
    diagnostics: [],
  };
}

function validateEntry(layer: string, address: string, entry: Entry): void {
  const where = `Container entry "${address}" of "${layer}"`;
  for (const key of ["only", "except"] as const) {
    const list = entry[key];
    if (list !== undefined && (!Array.isArray(list) || !list.every((id) => typeof id === "string"))) {
      throw new Error(`${where}: ${key} must be a list of child ids.`);
    }
  }
  if (entry.unique !== undefined && entry.unique !== "key") throw new Error(`${where}: unique must be "key".`);
  if (entry.when !== undefined) {
    if (typeof entry.when !== "object" || entry.when === null) throw new Error(`${where}: when must be a mapping.`);
    for (const key of Object.keys(entry.when)) {
      if (!CONDITION_KEYS.has(key)) throw new Error(`${where}: unknown condition "${key}".`);
    }
  }
  for (const [key, value] of Object.entries(entry)) {
    if (ENTRY_KEYS.has(key)) continue;
    if (!key.includes(".")) throw new Error(`${where}: unknown key "${key}"; child ids are namespaced ("addon.name").`);
    if (typeof value !== "object" || value === null || Array.isArray(value)) throw new Error(`${where}: child "${key}" must be a mapping.`);
  }
}
