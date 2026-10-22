import type { I18nResources } from "@angee/refine";
import { createInstance, type i18n } from "i18next";

/** Configure native translation for the app runtime and provider-less bindings.
 * @kind null
 * @reason Runtime infrastructure: composition, state, validation or lifecycle support without rendered anatomy.
 */
export function createAngeeI18nInstance(
  resources: I18nResources,
  locale = "en",
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
