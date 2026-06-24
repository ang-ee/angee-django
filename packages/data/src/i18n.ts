import type { I18nProvider } from "@refinedev/core";
import {
  interpolateMessage,
  type I18nResources,
  type MessageVars,
} from "@angee/sdk";

export interface AngeeI18nProviderOptions {
  locale?: string;
}

export function createAngeeI18nProvider(
  resources: I18nResources,
  options: AngeeI18nProviderOptions = {},
): I18nProvider {
  let locale = options.locale ?? "en";
  return {
    translate(key, vars, defaultMessage) {
      return translateAngeeMessage(resources, key, vars, defaultMessage);
    },
    changeLocale(nextLocale) {
      locale = nextLocale;
      return Promise.resolve(locale);
    },
    getLocale() {
      return locale;
    },
  };
}

export function translateAngeeMessage(
  resources: I18nResources,
  key: string,
  options?: unknown,
  defaultMessage?: string,
): string {
  const namespace = namespaceOption(options);
  const message =
    (namespace ? resources[namespace]?.[key] : undefined) ??
    messageFromNamespacedKey(resources, key) ??
    messageFromAnyNamespace(resources, key) ??
    defaultMessage ??
    key;
  return interpolateMessage(message, messageVars(options));
}

function messageFromNamespacedKey(
  resources: I18nResources,
  key: string,
): string | undefined {
  const [namespace, ...rest] = key.split(".");
  if (!namespace || rest.length === 0) return undefined;
  return resources[namespace]?.[rest.join(".")];
}

function messageFromAnyNamespace(
  resources: I18nResources,
  key: string,
): string | undefined {
  for (const messages of Object.values(resources)) {
    if (key in messages) return messages[key];
  }
  return undefined;
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
