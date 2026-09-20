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
        // refine calls this two ways, and its own `safeTranslate` helper spells
        // both out: `translate(key, options, defaultMessage)` and, when there
        // are no options, `translate(key, defaultMessage)`. Reading the second
        // argument as options only drops every default in the short form, so a
        // missing key reaches the screen as the key itself -- which is how the
        // create toast came to show `notifications.success`.
        const shorthand = typeof vars === "string";
        const options = shorthand ? undefined : vars;
        const fallback = shorthand ? vars : defaultMessage;
        const namespace = namespaceOption(options);
        const result = instance.t(key, {
          ...messageVars(options),
          ...(namespace ? { ns: namespace } : {}),
          ...(fallback ? { defaultValue: fallback } : {}),
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
    // Resource identifiers are `<schema>:<modelLabel>`, and refine builds i18n
    // keys out of them, so the default ":" namespace separator would read the
    // schema as a namespace and look up the wrong key. Namespaces reach this
    // instance through the `ns` option instead (see `namespaceOption`).
    nsSeparator: false,
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
