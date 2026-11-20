/**
 * The entry of a web form's public page: SurveyJS's renderer and stylesheet, the
 * deployment's themes, and the page itself (`./page`). It loads none of the
 * application: no React, no GraphQL, no session. The page runs under a content
 * security policy with no inline script, so everything it does starts here.
 */
import { applyAppearanceRoot, readAppearancePayload, resolveAppearance } from "@angee/ui/theme-runtime";
import { renderSurvey, unmountComponentAtNode } from "survey-js-ui";

import "survey-core/survey-core.css";
import "./surveyjs-bridge.css";
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
// Should the page fail before it can say anything, the document's own message is shown at once.
openPage({
  render(survey, host) {
    renderSurvey(survey, host);
    return () => void unmountComponentAtNode(host);
  },
  wear,
}).catch(() => document.body.classList.remove("starting"));
