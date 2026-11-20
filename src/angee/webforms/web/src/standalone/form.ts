/**
 * The public page of one web form.
 *
 * The address that serves this page also answers JSON: a GET describes the
 * version the form serves now and a POST keeps one submission. SurveyJS renders
 * the stored definition and owns the questions, their texts in each language,
 * validation in the browser and the completed page. This module connects it to
 * that address, to the deployment's themes and to the visitor's language, and
 * nothing more. It loads none of the application: no React, no GraphQL, no session.
 *
 * A form's texts are plain text: SurveyJS renders them as text, and no markdown
 * or HTML handler is installed for them here. Only its `html` elements and its
 * completed page are markup, which the server stores as simple markup; their
 * links are told how to open in `./markup`. This module writes its own messages
 * as text, and the page runs under a content security policy with no inline script.
 */
import { applyAppearanceRoot } from "@angee/ui/theme-runtime";
import { Model, settings, surveyLocalization } from "survey-core";
import { renderSurvey } from "survey-js-ui";
import { appearance } from "virtual:angee-appearance";

import "survey-core/survey-core.css";
import "./surveyjs-bridge.css";
import { answersToSend, restoreAnswers } from "./answers";
import { chooseLanguage, loadInterfaceStrings } from "./language";
import { linksInNewTab } from "./markup";

/** What the address answers to a JSON GET: the version the form serves now. */
interface FormDescription {
  version: number;
  /** The checked subset of a SurveyJS survey definition. */
  definition: object;
  languages: string[];
  default_language: string;
  /** The language this address opens the form in; empty leaves it to the visitor. */
  language: string;
  /** The theme the page wears; empty follows the deployment's default. */
  theme: string;
  /** The field a person never fills in; empty when the deployment sets none. */
  honeypot_field: string;
}

/** What the address answers when it refuses a submission. */
interface Refusal {
  message?: string;
  /** Refusals by question name, or by the part of the submission they are about. */
  fields?: Record<string, string[]>;
}

// Plain copy for the two states that arrive without a text of the form's own.
const CLOSED = "This form is not taking responses.";
const RETRY = "That did not work. Try again in a moment.";

const address = location.pathname;
// One id for the page's life: sending the same answers again is the same response.
const submissionId = crypto.randomUUID();
const dark = matchMedia("(prefers-color-scheme: dark)");
const mount = document.getElementById("form") as HTMLElement;
const notice = document.getElementById("notice") as HTMLElement;
const picker = document.getElementById("language") as HTMLSelectElement;
const honeypot = document.getElementById("honeypot") as HTMLInputElement;

/** The version on screen, its survey and the language it is shown in. */
let form: FormDescription | undefined;
let survey: Model;
let language = "";

/** Wear the form's theme, else the deployment's default, in the visitor's colour scheme. */
function wear(): void {
  applyAppearanceRoot({
    themeId: form?.theme || appearance.themeId || null,
    colorScheme: dark.matches ? "dark" : "light",
  });
}

/** Replace the form with one plain message. */
function say(text: string): void {
  mount.hidden = picker.hidden = true;
  notice.textContent = text;
  notice.hidden = document.body.hidden = false;
}

/** Ask the address for the version the form serves now; nothing when it takes no submissions. */
async function describe(): Promise<FormDescription | undefined> {
  const response = await fetch(address, { headers: { Accept: "application/json" } });
  if (response.status === 404) return undefined;
  if (!response.ok) throw new Error(`The form could not be loaded (${response.status}).`);
  return (await response.json()) as FormDescription;
}

/** A language in its own name, as its speakers read it. */
function ownName(tag: string): string {
  try {
    return new Intl.DisplayNames([tag], { type: "language" }).of(tag) ?? tag;
  } catch {
    return tag;
  }
}

/** Show the form in one of its languages, with SurveyJS's own strings for it. */
async function speak(tag: string): Promise<void> {
  // Without its strings a language still shows the form's own texts; SurveyJS's read English.
  const code = await loadInterfaceStrings(tag).catch(() => undefined);
  // SurveyJS reads its strings under the tag as the form writes it; its own code may be spelled another way.
  if (code && code !== tag) surveyLocalization.locales[tag] ??= surveyLocalization.locales[code];
  language = tag;
  // SurveyJS keeps the default language's texts under `default`, which is the empty locale.
  survey.locale = tag === form?.default_language ? "" : tag;
  picker.value = tag;
  document.documentElement.lang = tag;
  if (survey.title) document.title = survey.title;
}

/** Put each refusal on its question, on the page that asks it; return the ones about no question. */
function refuse(fields: Record<string, string[]>): string {
  const general: string[] = [];
  const refused = Object.entries(fields).flatMap(([name, messages]) => {
    // The text of an "Other" choice is kept beside its question.
    const suffix = settings.commentSuffix;
    const question = survey.getQuestionByName(name.endsWith(suffix) ? name.slice(0, -suffix.length) : name);
    if (!question) {
      general.push(...messages);
      return [];
    }
    question.addError(messages.join(" "));
    return [question];
  });
  const first = refused[0];
  if (first) {
    survey.currentPage = first.page;
    first.focus(true);
  }
  return general.join(" ");
}

/** Send the answers. Nothing comes back when they were kept; otherwise what to tell the visitor. */
async function send(): Promise<string | undefined> {
  const response = await fetch(address, {
    method: "POST",
    headers: { Accept: "application/json", "Content-Type": "application/json" },
    body: JSON.stringify({
      submission_id: submissionId,
      version: form?.version,
      language,
      answers: answersToSend(survey),
      ...(honeypot.name ? { [honeypot.name]: honeypot.value } : {}),
    }),
  }).catch(() => undefined);
  if (response?.status === 202) return undefined;
  const refusal = ((await response?.json().catch(() => undefined)) ?? {}) as Refusal;
  if (response?.status === 409) {
    // The form serves another version now: show it with the answers it still asks for, and say so on it.
    try {
      await open(survey.data);
    } catch {
      return RETRY;
    }
    if (form) survey.notify(refusal.message ?? RETRY, "error");
    return "";
  }
  return (refusal.fields ? refuse(refusal.fields) : refusal.message) ?? RETRY;
}

/** Render the version the form serves now, keeping the answers already given that it still asks for. */
async function open(answers: object = {}): Promise<void> {
  form = await describe();
  wear();
  if (!form) return say(CLOSED);
  surveyLocalization.defaultLocale = form.default_language;
  survey = new Model(form.definition);
  restoreAnswers(survey, answers);
  // The one hook SurveyJS passes its markup through: `html` elements and the completed page.
  survey.onProcessHtml.add((_survey, options) => {
    options.html = linksInNewTab(options.html);
  });
  // SurveyJS shows its completed page only when the answers were kept; a refusal returns to the form.
  survey.onCompleting.add(async (_survey, options) => {
    const refusal = await send();
    if (refusal === undefined) return;
    options.allow = false;
    options.message = refusal;
  });
  honeypot.name = form.honeypot_field;
  picker.replaceChildren(...form.languages.map((tag) => new Option(ownName(tag), tag)));
  await speak(form.languages.includes(language) ? language : chooseLanguage({
    requested: new URLSearchParams(location.search).get("lang"),
    served: form.language,
    preferred: navigator.languages,
    languages: form.languages,
    defaultLanguage: form.default_language,
  }));
  const host = document.createElement("div");
  mount.replaceChildren(host);
  renderSurvey(survey, host);
  picker.hidden = form.languages.length < 2;
  notice.hidden = true;
  mount.hidden = document.body.hidden = false;
}

picker.addEventListener("change", () => {
  void speak(picker.value);
  const url = new URL(location.href);
  url.searchParams.set("lang", picker.value);
  history.replaceState(null, "", url);
});
dark.addEventListener("change", wear);
open().catch(() => {
  wear();
  say(RETRY);
});
