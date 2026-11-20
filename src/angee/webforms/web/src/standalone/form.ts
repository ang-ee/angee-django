/**
 * The entry of a web form's public page. It loads none of the application: no
 * React, no GraphQL, no session. The page runs under a content security policy
 * with no inline script, so everything it does starts here.
 *
 * The entry is small on purpose. It asks for the form's description before
 * anything else of the page is fetched, and only then for SurveyJS and the rest
 * (`./open`), which the build has the document preload: the description and
 * the largest download arrive side by side, not one after the other.
 */
import "survey-core/survey-core.css";
import "./surveyjs-bridge.css";

const described = fetch(location.pathname, { headers: { Accept: "application/json" } });
// A request that fails is the page's to report, once it is there to report it.
described.catch(() => undefined);

// Should the page fail before it can say anything, the document's own message is shown at once.
import("./open").then(({ open }) => open(described)).catch(() => document.body.classList.remove("starting"));
