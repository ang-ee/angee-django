import type { I18nProvider } from "@refinedev/core";
import {
  createInstance,
  type i18n,
  type TOptions,
} from "i18next";

export type MessageVars = Record<string, string | number>;
export type I18nResources = Record<string, Record<string, string>>;
export type MessageResources = Record<string, string>;

export interface AngeeI18nProviderOptions {
  locale?: string;
}

export function createAngeeI18nProvider(
  resources: I18nResources,
  options: AngeeI18nProviderOptions = {},
): I18nProvider {
  const instance = createAngeeI18nInstance(resources, options.locale ?? "en");
  return {
    translate(key, vars, defaultMessage) {
      return translateWithInstance(instance, resources, key, vars, defaultMessage);
    },
    async changeLocale(nextLocale) {
      await instance.changeLanguage(nextLocale);
      return nextLocale;
    },
    getLocale() {
      return instance.language;
    },
  };
}

export function translateAngeeMessage(
  resources: I18nResources,
  key: string,
  options?: unknown,
  defaultMessage?: string,
): string {
  const instance = createAngeeI18nInstance(resources, "en");
  return translateWithInstance(instance, resources, key, options, defaultMessage);
}

export function interpolateMessage(template: string, vars: MessageVars): string {
  const instance = createAngeeI18nInstance(
    { translation: { value: template } },
    "en",
  );
  return instance.t("value", { ns: "translation", ...vars });
}

export function translateWithFallback(
  hostT: (key: string) => string,
  messages: MessageResources,
  key: string,
  vars: MessageVars = {},
): string {
  const fromHost = hostT(key);
  const resolved = fromHost === key ? (messages[key] ?? key) : fromHost;
  return interpolateMessage(resolved, vars);
}

function translateWithInstance(
  instance: i18n,
  resources: I18nResources,
  key: string,
  options?: unknown,
  defaultMessage?: string,
): string {
  const namespace = namespaceOption(options);
  const vars = messageVars(options);
  const target = namespace
    ? { key, namespace }
    : namespacedKey(resources, key) ?? { key };
  const result = instance.t(target.key, {
    ...vars,
    ...(target.namespace ? { ns: target.namespace } : {}),
    ...(defaultMessage ? { defaultValue: defaultMessage } : {}),
  } satisfies TOptions);
  return typeof result === "string" ? result : String(result);
}

function namespacedKey(
  resources: I18nResources,
  key: string,
): { key: string; namespace?: string } | undefined {
  const [namespace, ...rest] = key.split(".");
  if (namespace && rest.length > 0) {
    const nestedKey = rest.join(".");
    if (resources[namespace]?.[nestedKey] != null) {
      return { key: nestedKey, namespace };
    }
  }
  if (Object.values(resources).some((messages) => key in messages)) return { key };
  return undefined;
}

function createAngeeI18nInstance(
  resources: I18nResources,
  locale: string,
): i18n {
  const namespaces = Object.keys(resources).sort();
  const instance = createInstance();
  void instance.init({
    lng: locale,
    fallbackLng: "en",
    defaultNS: namespaces[0] ?? "translation",
    fallbackNS: namespaces,
    ns: namespaces,
    resources: {
      en: resources,
    },
    keySeparator: false,
    interpolation: {
      prefix: "{",
      suffix: "}",
      escapeValue: false,
    },
    returnNull: false,
    initAsync: false,
  });
  return instance;
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

function recordValue(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}
