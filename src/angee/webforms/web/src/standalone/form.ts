/**
 * The entry of a web form's public page: SurveyJS's renderer and stylesheet, the
 * deployment's themes, and the page itself (`./page`). It loads none of the
 * application: no React, no GraphQL, no session. The page runs under a content
 * security policy with no inline script, so everything it does starts here.
 */
import { applyAppearanceRoot } from "@angee/ui/theme-runtime";
import { renderSurvey, unmountComponentAtNode } from "survey-js-ui";
import { appearance } from "virtual:angee-appearance";

import "survey-core/survey-core.css";
import "./surveyjs-bridge.css";
import { openPage } from "./page";

const dark = matchMedia("(prefers-color-scheme: dark)");
let theme = "";

/** Wear the form's theme, else the deployment's default, in the visitor's colour scheme. */
function wear(themeId: string = theme): void {
  theme = themeId;
  applyAppearanceRoot({
    themeId: themeId || appearance.themeId || null,
    colorScheme: dark.matches ? "dark" : "light",
  });
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
