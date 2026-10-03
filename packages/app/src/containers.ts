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

import { orderById, positionSiblings } from "@angee/ui/lib/position";

import { DEPLOYMENT_LAYER_ID, assertMayAlter, layerAncestry, overridesField, type Layer } from "./layers";

export interface ContainerLayer extends Layer {
  containers?: ContainersDeclaration;
}

const FRAMEWORK = "framework";
const ENTRY_KEYS: ReadonlySet<string> = new Set(["only", "except", "when", "unique", "models"]);
const CHILD_KEYS: ReadonlySet<string> = new Set(["content", "sequence", "before", "after", "permission", "requiredFields", "impl", "variant", "key"]);
const ALTERATION_KEYS: ReadonlySet<string> = new Set(["sequence", "before", "after", "remove", "hide"]);
const CONDITION_KEYS: ReadonlySet<string> = new Set(["app", "route", "perspective"]);

type Entry = Record<string, unknown> & { only?: readonly string[]; except?: readonly string[]; when?: ContainerCondition; unique?: "key"; models?: true };

interface Child extends ComposedContainerChild {
  setBy: Record<string, string>;
}

/**
 * Compile the composed addons' containers.
 *
 * The framework's containers are declared up front; an addon declares a
 * container on one of its own (non-model) nodes by naming its address. A key in
 * the addon's own namespace declares a child; any other key alters a child of
 * an addon it depends on (or one of the framework's own children), and two
 * unrelated layers setting one field fail. `only`, `except` and `hide` are
 * render-time narrowing kept per layer, ranked by dependency, with their
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
  const declared: Record<string, ComposedContainers["declared"][string]> = {};
  const modelKinds = new Map<string, string>();
  const registerKind = (name: string, address: string): void => {
    const kind = modelKinds.get(name);
    if (kind && kind !== address) throw new Error(`Container name "#${name}" belongs to two kinds, "${kind}" and "${address}".`);
    modelKinds.set(name, address);
  };
  for (const container of core) {
    if (declared[container.address]) throw new Error(`Container "${container.address}" is declared twice.`);
    declared[container.address] = { owner: FRAMEWORK, models: container.models === true, ...(container.extras ? { extras: true } : {}) };
    if (container.models) registerKind(containerName(container.address), container.address);
  }
  // An id belongs to the addon whose id is its longest prefix (`appearance.integrate.x` is not appearance's).
  const layerIds = layers.map((layer) => layer.id).filter((id) => id !== DEPLOYMENT_LAYER_ID);
  const namespaceOf = (id: string): string | undefined => layerIds.reduce<string | undefined>((best, layer) =>
    (id === layer || id.startsWith(`${layer}.`)) && (best === undefined || layer.length > best.length) ? layer : best, undefined);
  const owns = (layer: string, id: string): boolean => layer !== DEPLOYMENT_LAYER_ID && namespaceOf(id) === layer;
  const nodeOf = (address: string): string => address.slice(0, address.length - containerName(address).length - 1);
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

  // An addon declares containers on its own nodes, which are never models, before
  // any address resolves, so dependents may contribute in any order and a
  // misspelt model container fails instead of declaring a new one.
  for (const layer of layers) {
    for (const [address, entry] of entriesOf(layer)) {
      const name = containerName(address);
      const node = nodeOf(address);
      if (isModelNode(node) || !owns(layer.id, node) || (modelKinds.get(name) === address)) continue;
      const known = declared[address];
      if (known && known.owner !== layer.id) throw new Error(`Container "${address}" is declared by "${known.owner}" and "${layer.id}".`);
      const models = entry.models === true || known?.models === true;
      const unique = entry.unique ?? known?.unique;
      declared[address] = { owner: layer.id, models, ...(unique ? { unique } : {}) };
      if (models) registerKind(name, address);
    }
  }
  for (const address of Object.keys(declared)) {
    const kind = modelKinds.get(containerName(address));
    if (kind && kind !== address) {
      throw new Error(`Container "${address}" shares its name with the model kind "${kind}"; name it apart.`);
    }
  }

  // A model address resolves through the kind owning its name; anything else is an addon's own container.
  const resolveAddress = (layer: string, address: string): string => {
    const name = containerName(address);
    const node = nodeOf(address);
    const kind = modelKinds.get(name);
    if (kind) {
      if (node === nodeOf(kind)) return kind;
      if (!isModelNode(node)) throw new Error(`Addon "${layer}" names "${address}", but "#${name}" is a model kind ("${kind}") and "${node}" is not a model.`);
      return `${canonicalizeModel(node)}#${name}`;
    }
    if (isModelNode(node)) throw new Error(`Addon "${layer}" names unknown container "${address}": models have no "#${name}".`);
    if (!declared[address]) throw new Error(`Addon "${layer}" names unknown container "${address}".`);
    return address;
  };
  const familyOf = (address: string): string => modelKinds.get(containerName(address)) ?? address;
  // Two addresses of a family render together when one is the kind's own: a page
  // merges the kind with its model's addresses, never two unrelated models.
  const meet = (left: string, right: string): boolean =>
    left === right || left === familyOf(left) || right === familyOf(right);

  // Children by address and, per family (a kind and its model addresses), by id.
  const children = new Map<string, Child>();
  const families = new Map<string, Map<string, Child[]>>();
  const childKey = (address: string, id: string): string => `${address}/${id}`;
  const addChild = (child: Child, where: string): void => {
    const family = families.get(familyOf(child.address)) ?? new Map<string, Child[]>();
    const same = family.get(child.id) ?? [];
    const clash = same.find((other) => meet(other.address, child.address));
    if (clash) {
      throw new Error(clash.address === child.address
        ? `${where} redefines child "${child.id}" of container "${child.address}".`
        : `${where} declares child "${child.id}" at "${clash.address}" and "${child.address}", which render together; one id per container.`);
    }
    family.set(child.id, [...same, child]);
    families.set(familyOf(child.address), family);
    children.set(childKey(child.address, child.id), child);
  };
  for (const container of core) {
    for (const [id, child] of Object.entries(container.children ?? {})) {
      addChild({ ...child, id, owner: FRAMEWORK, address: container.address,
        setBy: Object.fromEntries(Object.keys(child).map((field) => [field, FRAMEWORK])) }, "The framework");
    }
  }
  const rules = new Map<string, ContainerRule[]>();
  const removed = new Map<string, ComposedContainers["removed"][number] & { owner: string }>();
  const diagnostics: string[] = [];
  const pending: { layer: string; address: string; id: string; alteration: ContainerAlteration; when?: ContainerCondition }[] = [];
  const pendingRules: { layer: string; address: string; entry: Entry }[] = [];

  for (const layer of layers) {
    for (const [rawAddress, entry] of entriesOf(layer)) {
      const address = resolveAddress(layer.id, rawAddress);
      validateEntry(layer.id, rawAddress, entry);
      if ((entry.unique !== undefined || entry.models !== undefined) && declared[address]?.owner !== layer.id) {
        throw new Error(`Addon "${layer.id}" sets unique or models on container "${rawAddress}" it does not own.`);
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
          const child = value as ContainerChild;
          for (const field of Object.keys(child)) {
            if (!CHILD_KEYS.has(field)) throw new Error(`Child "${id}" of "${rawAddress}" in "${layer.id}" has unknown key "${field}".`);
          }
          addChild({ ...child, id, owner: layer.id, address,
            setBy: Object.fromEntries(Object.keys(child).map((field) => [field, layer.id])) }, `Addon "${layer.id}"`);
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

  // Rules apply in dependency order: a layer ranks after everything it depends on.
  const rankOf = (layer: string): number => ancestors.get(layer)?.size ?? 0;
  const addRule = (address: string, rule: ContainerRule): void => {
    rules.set(address, [...(rules.get(address) ?? []), rule]);
  };
  const dependentsOf = (layer: string): string[] =>
    layers.filter((other) => ancestors.get(other.id)?.has(layer)).map((other) => other.id);

  // The children an alteration at `address` reaches: at the kind's own address,
  // the id at every address; at a model's, the id there, else the kind's, else
  // the one model address declaring it (its MTI parent's).
  const targetsOf = (layer: string, address: string, id: string): Child[] => {
    const family = familyOf(address);
    const all = families.get(family)?.get(id) ?? [];
    if (address === family) return all;
    const here = all.filter((child) => child.address === address);
    if (here.length) return here;
    const kind = all.filter((child) => child.address === family);
    if (kind.length || all.length < 2) return kind.length ? kind : all;
    throw new Error(`Addon "${layer}" alters child "${id}" at "${address}", which ${all.map((child) => `"${child.address}"`).join(" and ")} declare; alter it at one of them.`);
  };
  for (const { layer, address, id, alteration, when } of pending) {
    const targets = targetsOf(layer, address, id);
    if (!targets.length) {
      const gone = [...removed.values()].find((entry) => entry.id === id && familyOf(entry.address) === familyOf(address));
      // Removing again is idempotent, for any layer that may alter the child.
      if (gone && gone.owner !== FRAMEWORK) assertMayAlter(ancestors, layer, gone.owner, `child "${id}" of container "${address}"`);
      if (gone && alteration.remove) continue;
      throw new Error(gone
        ? `Addon "${layer}" alters child "${id}" of container "${address}", which "${gone.by}" removed.`
        : `Addon "${layer}" alters unknown child "${id}" of container "${address}".`);
    }
    for (const child of targets) {
      // The framework's own children are every addon's to adjust, as its containers are.
      if (child.owner !== FRAMEWORK) assertMayAlter(ancestors, layer, child.owner, `child "${id}" of container "${address}"`);
      for (const field of ["sequence", "before", "after"] as const) {
        if (alteration[field] === undefined) continue;
        if (child.address !== address) throw new Error(`Addon "${layer}" positions child "${id}" at "${address}"; position it at "${child.address}".`);
        const previous = child.setBy[field] === FRAMEWORK ? undefined : child.setBy[field];
        if (overridesField(ancestors, previous, layer, `child "${id}" of "${address}" ${field}`)) {
          (child as unknown as Record<string, unknown>)[field] = alteration[field];
          child.setBy[field] = layer;
        }
      }
      if (alteration.remove) {
        children.delete(childKey(child.address, id));
        const family = families.get(familyOf(child.address))!;
        const rest = family.get(id)!.filter((other) => other !== child);
        if (rest.length) family.set(id, rest); else family.delete(id);
        removed.set(childKey(child.address, id), { address: child.address, id, by: layer, owner: child.owner });
      }
    }
    if (alteration.hide !== undefined) {
      addRule(address, { layer, rank: rankOf(layer), ...(when ? { when } : {}),
        ...(alteration.hide ? { hide: [id] } : { show: [id] }), exempt: [] });
    }
  }

  for (const { layer, address, entry } of pendingRules) {
    const family = familyOf(address);
    const owner = declared[family]?.owner ?? FRAMEWORK;
    if (owner !== FRAMEWORK) assertMayAlter(ancestors, layer, owner, `container "${address}"`);
    const ids = new Set([...(families.get(family)?.values() ?? [])].flat().flatMap((child) => [child.id, ...(child.variant ? [child.variant.of] : [])]));
    for (const id of [...(entry.only ?? []), ...(entry.except ?? [])]) {
      if (ids.has(id)) continue;
      const unknown = `Addon "${layer}" narrows container "${address}" to unknown child "${id}"`;
      // A container that takes children at render time (a page's chatter tabs) admits
      // ids composition cannot know; developer mode lists them in case of a typo.
      if (!declared[family]?.extras) throw new Error(`${unknown}.`);
      diagnostics.push(`${unknown}, unless a page adds it.`);
    }
    addRule(address, {
      layer,
      rank: rankOf(layer),
      ...(entry.when ? { when: entry.when } : {}),
      ...(entry.only ? { only: entry.only } : {}),
      ...(entry.except ? { except: entry.except } : {}),
      exempt: dependentsOf(layer),
    });
  }

  // Variants, positions and unique keys are checked against the final inventory,
  // per group a page renders together: the kind's children with one model's.
  for (const [family, members] of families) {
    const all = [...members.values()].flat();
    for (const child of all) {
      if (child.variant && !members.has(child.variant.of)) throw new Error(`Child "${child.id}" of "${child.address}" is a variant of unknown child "${child.variant.of}".`);
      const anchor = child.before ?? child.after;
      if (anchor !== undefined && !members.has(anchor)) diagnostics.push(`Child "${child.id}" of "${child.address}" positions itself against "${anchor}", which "${family}" does not hold.`);
    }
    const models = [...new Set(all.map((child) => child.address))].filter((address) => address !== family);
    const groups = models.length ? models.map((model) => all.filter((child) => meet(child.address, model))) : [all];
    for (const group of groups) {
      const where = group.find((child) => child.address !== family)?.address ?? family;
      const variantKeys = new Map<string, string>();
      for (const child of group) {
        if (!child.variant) continue;
        const key = `${child.variant.of}\0${child.variant.impl}`;
        if (variantKeys.has(key)) throw new Error(`Children "${variantKeys.get(key)}" and "${child.id}" of "${where}" are both variants of "${child.variant.of}" for impl "${child.variant.impl}".`);
        variantKeys.set(key, child.id);
      }
      positionSiblings(orderById(group), `Children of "${where}"`);
      if (declared[family]?.unique === "key") {
        const keys = new Map<string, string>();
        for (const child of group) {
          if (child.key === undefined) throw new Error(`Child "${child.id}" of "${child.address}" needs a key: the container renders one child per key.`);
          if (keys.has(child.key)) throw new Error(`Children "${keys.get(child.key)}" and "${child.id}" of "${where}" share key "${child.key}".`);
          keys.set(child.key, child.id);
        }
      }
    }
  }

  const composed: Record<string, ComposedContainerChild[]> = {};
  for (const child of children.values()) {
    const { setBy: _setBy, ...plain } = child;
    composed[child.address] = [...(composed[child.address] ?? []), plain];
  }
  return {
    declared,
    children: composed,
    rules: Object.fromEntries(rules),
    removed: [...removed.values()].map(({ owner: _owner, ...entry }) => entry),
    provenance: Object.fromEntries([...children.values()].map((child) => [childKey(child.address, child.id), { ...child.setBy }])),
    diagnostics,
  };
}

/** A model spelling's last segment is capitalised (`projects.Task`, `Task`); addon nodes are not. */
function isModelNode(node: string): boolean {
  return /(^|\.)[A-Z][^.]*$/.test(node);
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
  if (entry.models !== undefined && entry.models !== true) throw new Error(`${where}: models must be true.`);
  if (entry.when !== undefined) {
    if (typeof entry.when !== "object" || entry.when === null) throw new Error(`${where}: when must be a mapping.`);
    for (const [key, value] of Object.entries(entry.when)) {
      if (!CONDITION_KEYS.has(key)) throw new Error(`${where}: unknown condition "${key}".`);
      if (typeof value !== "string" && !(Array.isArray(value) && value.every((item) => typeof item === "string"))) {
        throw new Error(`${where}: condition "${key}" must be an id or a list of ids.`);
      }
    }
  }
  for (const [key, value] of Object.entries(entry)) {
    if (ENTRY_KEYS.has(key)) continue;
    // A bare key can only alter one of the framework's own children (`list`, `board`, …).
    if (typeof value !== "object" || value === null || Array.isArray(value)) throw new Error(`${where}: child "${key}" must be a mapping.`);
  }
}
