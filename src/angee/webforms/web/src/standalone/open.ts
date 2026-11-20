/**
 * The rest of a web form's public page, loaded beside the form's description:
 * SurveyJS's renderer, the deployment's themes and the page itself (`./page`).
 */
import { applyAppearanceRoot, readAppearancePayload, resolveAppearance } from "@angee/ui/theme-runtime";
import { renderSurvey, unmountComponentAtNode } from "survey-js-ui";

import { openPage } from "./page";

const dark = matchMedia("(prefers-color-scheme: dark)");
// The deployment's appearance, which the build writes into the document as data.
const deployment = readAppearancePayload();
let theme = "";

/**
 * Wear the form's theme, else the deployment's, as the application itself
 * resolves it: under the deployment's colour scheme when it pins one, the
 * visitor's when it follows the system, and with the deployment's own tokens.
 */
function wear(themeId: string = theme): void {
  theme = themeId;
  applyAppearanceRoot(deployment
    ? resolveAppearance(deployment, { themeId: themeId || undefined, prefersDark: () => dark.matches })
    // A document the build did not prepare names only the theme and the visitor's scheme.
    : { themeId: themeId || null, colorScheme: dark.matches ? "dark" : "light" });
}

dark.addEventListener("change", () => wear());

/** Open the form in the document, starting from the description the entry already asked for. */
export function open(described: Promise<Response>): Promise<void> {
  return openPage({
    described,
    render(survey, host) {
      renderSurvey(survey, host);
      return () => void unmountComponentAtNode(host);
    },
    wear,
  });
}
