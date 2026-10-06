import type { I18nProvider } from "@refinedev/core";
import type { TOptions, i18n } from "i18next";
import { recordValue, type I18nResources, type MessageVars } from "@angee/refine";
import { createAngeeI18nInstance } from "@angee/ui/runtime";
import type { AppVocabulary, RuntimeVocabulary } from "@angee/ui/runtime";
import { canonicalModelLabel, ResourceQuery, type DataResourceMetadata, type ResourceVocabulary } from "@angee/metadata";
import type { MenuTree } from "@angee/ui/chrome/menu-tree";
import type { AddonRoute } from "../define-addon";
import { isTone } from "@angee/ui/lib";

export interface AngeeI18nProviderOptions {
  locale?: string;
}

export interface AngeeI18nRuntime {
  instance: i18n;
  provider: I18nProvider;
}

export function createAngeeI18nRuntime(
  resources: I18nResources,
  options: AngeeI18nProviderOptions = {},
): AngeeI18nRuntime {
  const instance = createAngeeI18nInstance(resources, options.locale ?? "en");
  return runtimeForInstance(instance);
}

function runtimeForInstance(instance: i18n, languageOwner = instance): AngeeI18nRuntime {
  return {
    instance,
    provider: {
      translate(key, vars, defaultMessage) {
        const namespace = namespaceOption(vars);
        const result = instance.t(key, {
          ...messageVars(vars),
          ...(namespace ? { ns: namespace } : {}),
          ...(defaultMessage ? { defaultValue: defaultMessage } : {}),
        } satisfies TOptions);
        return typeof result === "string" ? result : String(result);
      },
      async changeLocale(nextLocale) {
        await languageOwner.changeLanguage(nextLocale);
        return nextLocale;
      },
      getLocale() {
        return instance.language;
      },
    },
  };
}

function namespaceOption(options: unknown): string | undefined {
  const namespace = recordValue(options)?.namespace;
  return typeof namespace === "string" ? namespace : undefined;
}

function messageVars(options: unknown): MessageVars {
  const record = recordValue(options);
  if (!record) return {};
  return Object.fromEntries(
    Object.entries(record).filter((entry): entry is [string, string | number] => {
      const value = entry[1];
      return typeof value === "string" || typeof value === "number";
    }),
  );
}

/**
 * Validate scoped overrides against the composed owners, then resolve by
 * specificity: the scopes of every app on the page's trail, outermost first,
 * then route scopes, nearer routes later and, per route, inner apps later.
 */
export function composeAppVocabulary(
  base: I18nResources,
  declarations: readonly AppVocabulary[],
  resources: readonly DataResourceMetadata[],
  menus: MenuTree,
  routes: readonly AddonRoute[],
): (apps?: readonly string[], route?: string) => { i18n: AngeeI18nRuntime; vocabulary: RuntimeVocabulary } {
  const routesByName = new Map(routes.map((route) => [route.name, route]));
  const scopes = new Map<string, AppVocabulary>();
  const host = base;
  const languageOwner = createAngeeI18nInstance(host);
  // Any app a page's trail can hold: a root or an included app, flattened ones too.
  const appIds = menus.appIds();
  for (const declaration of declarations) {
    if (!appIds.has(declaration.app)) {
      throw new Error(`Vocabulary references unknown app "${declaration.app}".`);
    }
    if (declaration.route && !routesByName.has(declaration.route)) {
      throw new Error(`Vocabulary references unknown route "${declaration.route}".`);
    }
    if (declaration.messages?.ui) throw new Error('Scoped vocabulary cannot override the reserved "ui" namespace.');
    overrideMessages(base, declaration.messages ?? {});
    const normalized: Record<string, ResourceVocabulary> = {};
    for (const [spelling, words] of Object.entries(declaration.resources ?? {})) {
      const model = canonicalModelLabel(resources, spelling);
      const modelResources = resources.filter((resource) => resource.modelLabel === model);
      const fields = new Set(modelResources.flatMap((resource) => [
        ...(resource.fields ?? []).map((field) => field.name),
        ...Object.entries(ResourceQuery.from(resource).fields)
          .filter(([, field]) => field.filter?.operators.length).map(([name]) => name),
      ]));
      for (const [field, word] of Object.entries(words.fields ?? {})) {
        if (!fields.has(field)) throw new Error(`Vocabulary references unknown field "${model}.${field}".`);
        if (typeof word !== "string") for (const [value, tone] of Object.entries(word.tones ?? {})) {
          if (!value.trim() || !isTone(tone)) throw new Error(`Vocabulary field "${model}.${field}" names an unknown tone "${String(tone)}" for "${value}".`);
        }
      }
      const relations = new Set(modelResources.flatMap((resource) => (resource.grantable ?? []).map((relation) => relation.relation)));
      for (const relation of Object.keys(words.relations ?? {})) {
        if (!relations.has(relation)) throw new Error(`Vocabulary references unknown relation "${model}.${relation}".`);
      }
      if (normalized[model]) throw new Error(`Vocabulary claims resource "${model}" twice.`);
      normalized[model] = words;
    }
    for (const id of Object.keys(declaration.menus ?? {})) {
      if (!menus.byId.has(id)) throw new Error(`Vocabulary references unknown menu "${id}".`);
    }
    const key = `${declaration.app}\0${declaration.route ?? ""}`;
    // Each scope has one declaration owner; app and route scopes compose below.
    if (scopes.has(key)) throw new Error(`Vocabulary scope "${declaration.app}:${declaration.route ?? "*"}" is declared twice.`);
    scopes.set(key, { ...declaration, resources: normalized });
  }
  const cache = new Map<string, { i18n: AngeeI18nRuntime; vocabulary: RuntimeVocabulary }>();
  return (apps = [], routeName) => {
    const key = `${apps.join("\0")}\0\0${routeName ?? ""}`;
    const cached = cache.get(key);
    if (cached) return cached;
    const routeNames: string[] = [];
    let route = routeName ? routesByName.get(routeName) : undefined;
    while (route && !routeNames.includes(route.name)) {
      routeNames.unshift(route.name);
      route = route.parent ? routesByName.get(route.parent) : undefined;
    }
    const chain = ["", ...routeNames].flatMap((name) => apps.flatMap((app) => scopes.get(`${app}\0${name}`) ?? []));
    let messages = host;
    const vocabulary: { resources: Record<string, ResourceVocabulary>; menus: Record<string, string> } = { resources: {}, menus: {} };
    for (const scope of chain) {
      messages = overrideMessages(messages, scope.messages ?? {});
      Object.assign(vocabulary.menus, scope.menus);
      for (const [model, words] of Object.entries(scope.resources ?? {})) {
        const inherited = vocabulary.resources[model];
        const fields = { ...inherited?.fields, ...words.fields };
        for (const [field, word] of Object.entries(words.fields ?? {})) {
          const previous = inherited?.fields?.[field];
          if (previous !== undefined && (typeof word === "object" || typeof previous === "object")) {
            const inheritedField = typeof previous === "string" ? { label: previous, tones: undefined } : previous;
            const overrideField = typeof word === "string" ? { label: word, tones: undefined } : word;
            fields[field] = { ...inheritedField, ...overrideField,
              tones: { ...inheritedField.tones, ...overrideField.tones } };
          }
        }
        vocabulary.resources[model] = { ...inherited, ...words, fields };
      }
    }
    const instance = languageOwner.cloneInstance({ forkResourceStore: true, lng: languageOwner.language });
    for (const [namespace, bundle] of Object.entries(messages)) {
      instance.addResourceBundle("en", namespace, bundle, true, true);
    }
    // Native i18next instances isolate copy; one native language owner spans scopes.
    languageOwner.on("languageChanged", (language) => {
      if (instance.language !== language) void instance.changeLanguage(language);
    });
    instance.on("languageChanged", (language) => {
      if (languageOwner.language !== language) void languageOwner.changeLanguage(language);
    });
    const resolved = { i18n: runtimeForInstance(instance, languageOwner), vocabulary };
    cache.set(key, resolved);
    return resolved;
  };
}

function overrideMessages(base: I18nResources, overrides: I18nResources): I18nResources {
  const result = { ...base };
  for (const [namespace, messages] of Object.entries(overrides)) {
    for (const key of Object.keys(messages)) {
      if (!Object.prototype.hasOwnProperty.call(base[namespace] ?? {}, key)) {
        throw new Error(`Vocabulary references unknown i18n key "${namespace}.${key}".`);
      }
    }
    result[namespace] = { ...base[namespace], ...messages };
  }
  return result;
}
