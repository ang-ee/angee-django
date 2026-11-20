/**
 * The public page of one web form.
 *
 * The address that serves the page also answers JSON: a GET describes the
 * version the form serves now and a POST keeps one submission. SurveyJS renders
 * the stored definition and owns the questions, their texts in each language,
 * validation in the browser and the completed page. This module connects it to
 * that address and to the visitor's language, and nothing more.
 *
 * A form's texts are plain text: SurveyJS renders them as text, and no markdown
 * or HTML handler is installed for them here. Only its `html` elements and its
 * completed page are markup, which the server stores as simple markup; their
 * links are told how to open in `./markup`. This module writes its own messages
 * as text.
 */
import { Model, settings, surveyLocalization } from "survey-core";

import { answersToSend, restoreAnswers } from "./answers";
import { chooseLanguage, loadInterfaceStrings } from "./language";
import { linksInNewTab } from "./markup";
import { pageMessages, type PageMessages } from "./messages";
import { newSubmissionId, readDescription, readRefusal, type FormDescription } from "./wire";

/** What the page's entry gives it: SurveyJS's renderer and the deployment's themes. */
export interface PageParts {
  /** Render a survey into an element; the answer takes that rendering down again. */
  render(survey: Model, host: HTMLElement): () => void;
  /** Wear a form's theme; an empty id follows the deployment's default. */
  wear(themeId: string): void;
  /** The form's description, when the entry already asked the address for it. */
  described?: Promise<Response>;
}

/** The document shows nothing while it carries this class; see `form.css`. */
const STARTING = "starting";

// A refusal about no question is a sentence to read, and SurveyJS shows its notices for two seconds.
const NOTICE_LIFETIME = 10_000;

/** A language in its own name, as its speakers read it. */
function ownName(tag: string): string {
  try {
    return new Intl.DisplayNames([tag], { type: "language" }).of(tag) ?? tag;
  } catch {
    return tag;
  }
}

/** Open the form this address serves in the document, and keep it open until its answers are kept. */
export async function openPage({ render, wear, described: asked }: PageParts): Promise<void> {
  const address = location.pathname;
  const wanted = new URLSearchParams(location.search).get("lang");
  // One id for the page's life: sending the same answers again is the same response.
  const submissionId = newSubmissionId();
  const mount = document.getElementById("form") as HTMLElement;
  const notice = document.getElementById("notice") as HTMLElement;
  const picker = document.getElementById("language") as HTMLSelectElement;
  const honeypot = document.getElementById("honeypot") as HTMLInputElement;
  settings.notifications.lifetime = NOTICE_LIFETIME;

  /** The version on screen, its survey, the language it is shown in and how often that was chosen. */
  let form: FormDescription | undefined;
  let survey: Model | undefined;
  let language = "";
  let choices = 0;
  /** Takes the survey on screen down: its elements first, then the model they read. */
  let takeDown: (() => void) | undefined;
  /** Whether the visitor turned to the page being drawn, and so follows it with the focus. */
  let turned = false;

  /** The page's own sentences: in the form's language once one is shown, else in the visitor's. */
  const text = (): PageMessages => pageMessages(language ? [language] : [wanted, ...navigator.languages]);

  /** Replace the form with one plain message. */
  function say(message: string): void {
    takeDown?.();
    takeDown = survey = undefined;
    mount.replaceChildren();
    mount.hidden = picker.hidden = true;
    notice.textContent = message;
    notice.hidden = false;
    document.body.classList.remove(STARTING);
  }

  /** Ask the address for the version the form serves now; nothing when it takes no submissions. */
  async function describe(): Promise<FormDescription | undefined> {
    // The entry's own request answers the first time; a later version is asked for anew.
    const response = await (asked ?? fetch(address, { headers: { Accept: "application/json" } }));
    asked = undefined;
    if (response.status === 404) return undefined;
    if (!response.ok) throw new Error(`The form could not be loaded (${response.status}).`);
    return readDescription(await response.json());
  }

  /** Show the form in one of its languages, with SurveyJS's own strings for it. */
  async function speak(tag: string): Promise<void> {
    const choice = ++choices;
    // Without its strings a language still shows the form's own texts; SurveyJS's read English.
    const code = await loadInterfaceStrings(tag).catch(() => undefined);
    // Strings arrive in any order: only the last language chosen is shown.
    if (choice !== choices || !form || !survey) return;
    // SurveyJS reads its strings under the tag as the form writes it; its own code may be spelled another way.
    if (code && code !== tag) surveyLocalization.locales[tag] ??= surveyLocalization.locales[code];
    language = tag;
    // SurveyJS keeps the default language's texts under `default`, which is the empty locale.
    survey.locale = tag === form.default_language ? "" : tag;
    picker.value = tag;
    picker.setAttribute("aria-label", text().language);
    document.documentElement.lang = tag;
    document.title = survey.title;
  }

  /** Put each refusal on its question, on the page that asks it; return the ones about no question. */
  function refuse(asking: Model, fields: Record<string, string[]>): string {
    const general: string[] = [];
    const refused = Object.entries(fields).flatMap(([name, messages]) => {
      // The text of an "Other" choice is kept beside its question.
      const suffix = settings.commentSuffix;
      const question = asking.getQuestionByName(name.endsWith(suffix) ? name.slice(0, -suffix.length) : name);
      if (!question) {
        general.push(...messages);
        return [];
      }
      question.addError(messages.join(" "));
      return [question];
    });
    const first = refused[0];
    if (first) {
      asking.currentPage = first.page;
      // The refused question takes the focus on the page it is on, not that page's title.
      turned = false;
      first.focus(true);
    }
    return general.join(" ");
  }

  /** Send a survey's answers. Nothing comes back when they were kept; otherwise what to tell the visitor. */
  async function send(asking: Model): Promise<string | undefined> {
    const response = await fetch(address, {
      method: "POST",
      headers: { Accept: "application/json", "Content-Type": "application/json" },
      body: JSON.stringify({
        submission_id: submissionId,
        version: form?.version,
        language,
        answers: answersToSend(asking),
        ...(honeypot.name ? { [honeypot.name]: honeypot.value } : {}),
      }),
    }).catch(() => undefined);
    if (response?.status === 202) return undefined;
    if (response?.status === 404) {
      // The form stopped taking submissions while it was being filled in.
      say(text().closed);
      return "";
    }
    if (response?.status === 409) {
      // The form serves another version now: show it with the answers it still asks for, and say so on it.
      await open(asking.data as object);
      survey?.notify(text().changed, "info");
      return "";
    }
    const refusal = readRefusal(await response?.json().catch(() => undefined));
    // SurveyJS has the sentence for a submission that could not be kept, in the form's language.
    return (refusal.fields ? refuse(asking, refusal.fields) : refusal.message) ?? asking.getLocalizationString("savingDataError");
  }

  /** Render the version the form serves now, keeping the answers already given that it still asks for. */
  async function open(answers: object = {}): Promise<void> {
    const described = await describe();
    form = described;
    wear(described?.theme ?? "");
    if (!described) return say(text().closed);
    surveyLocalization.defaultLocale = described.default_language;
    const next = new Model(described.definition);
    restoreAnswers(next, answers);
    // The one hook SurveyJS passes its markup through: `html` elements and the completed page.
    next.onProcessHtml.add((_survey, options) => {
      options.html = linksInNewTab(options.html);
    });
    // SurveyJS scrolls a page the visitor turns to up to its top, and leaves the focus on the
    // button that turned it. The focus follows to the page's title, or to the page when it has
    // none: reading continues from there, and no keyboard opens over a phone's screen.
    next.onCurrentPageChanged.add(() => {
      turned = true;
    });
    next.onAfterRenderPage.add((_survey, options) => {
      if (!turned) return;
      turned = false;
      const start = document.getElementById(options.page.ariaTitleId) ?? options.htmlElement;
      start.tabIndex = -1;
      start.focus({ preventScroll: true });
    });
    // SurveyJS shows its completed page only when the answers were kept; a refusal returns to the form.
    // SurveyJS holds its navigation until this handler answers, so it answers whatever happens.
    // A survey can be taken down while it waits here (the form changed, or closed): it is then
    // finished with only once SurveyJS has read this handler's answer.
    let answering = false;
    let retired = false;
    next.onCompleting.add(async (_survey, options) => {
      let refusal: string | undefined = next.getLocalizationString("savingDataError");
      answering = true;
      try {
        refusal = await send(next);
      } catch {
        // Whatever went wrong, the answers were not kept: the plain sentence stands.
      } finally {
        if (refusal !== undefined) {
          options.allow = false;
          options.message = refusal;
        }
        answering = false;
        // SurveyJS reads the answer as this handler returns; a task later it is done with the survey.
        if (retired) setTimeout(() => next.dispose());
      }
    });
    survey = next;
    honeypot.name = described.honeypot_field;
    picker.replaceChildren(...described.languages.map((tag) => {
      const option = document.createElement("option");
      option.value = tag;
      option.textContent = ownName(tag);
      return option;
    }));
    await speak(described.languages.includes(language) ? language : chooseLanguage({
      requested: wanted,
      served: described.language,
      preferred: navigator.languages,
      languages: described.languages,
      defaultLanguage: described.default_language,
    }));
    // The version that was on screen is finished with.
    takeDown?.();
    const host = document.createElement("div");
    mount.replaceChildren(host);
    const remove = render(next, host);
    takeDown = () => {
      remove();
      if (answering) retired = true;
      else next.dispose();
    };
    picker.hidden = described.languages.length < 2;
    notice.hidden = true;
    mount.hidden = false;
    document.body.classList.remove(STARTING);
  }

  picker.addEventListener("change", () => {
    void speak(picker.value);
    const url = new URL(location.href);
    url.searchParams.set("lang", picker.value);
    history.replaceState(null, "", url);
  });
  try {
    await open();
  } catch {
    wear("");
    say(text().unavailable);
  }
}
