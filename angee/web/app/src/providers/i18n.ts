import type { I18nProvider } from "@refinedev/core";
import { createInstance, type i18n, type TOptions } from "i18next";
import { recordValue, type I18nResources, type MessageVars } from "@angee/refine";
import type { RuntimeI18n } from "@angee/ui/runtime";

export interface AngeeI18nProviderOptions {
  locale?: string;
}

export interface AngeeI18nRuntime {
  instance: RuntimeI18n;
  provider: I18nProvider;
}

export function createAngeeI18nRuntime(
  resources: I18nResources,
  options: AngeeI18nProviderOptions = {},
): AngeeI18nRuntime {
  const instance = createAngeeI18nInstance(resources, options.locale ?? "en");
  return {
    instance: instance as RuntimeI18n,
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
        await instance.changeLanguage(nextLocale);
        return nextLocale;
      },
      getLocale() {
        return instance.language;
      },
    },
  };
}

export function createAngeeI18nProvider(
  resources: I18nResources,
  options: AngeeI18nProviderOptions = {},
): I18nProvider {
  return createAngeeI18nRuntime(resources, options).provider;
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
    resources: { en: resources },
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
