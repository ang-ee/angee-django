// @vitest-environment happy-dom

import { Model } from "survey-core";
import { renderSurvey, unmountComponentAtNode } from "survey-js-ui";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import page from "./form.html?raw";
import { openPage } from "./page";

/** The page's own document, without the script that starts it. */
const DOCUMENT = /<body class="([^"]*)">([\s\S]*)<\/body>/.exec(page)!;

const FIRST = {
  title: { default: "Encuesta", en: "Survey" },
  pages: [{
    elements: [
      { type: "text", name: "name", title: { default: "Nombre", en: "Name" } },
      { type: "text", name: "nickname" },
      { type: "radiogroup", name: "colour", choices: ["red", "blue"], showOtherItem: true },
    ],
  }],
};

/** The same form after staff removed a question and the "Other" choice. */
const SECOND = {
  title: { default: "Encuesta", en: "Survey" },
  pages: [{
    elements: [
      { type: "text", name: "name", title: { default: "Nombre", en: "Name" } },
      { type: "radiogroup", name: "colour", choices: ["red", "blue"] },
    ],
  }],
};

/** A form in two pages, with the progress the server's subset admits. */
const PAGED = {
  title: { default: "Encuesta", en: "Survey" },
  showProgressBar: true,
  progressBarLocation: "belowHeader",
  progressBarType: "pages",
  pages: [
    { name: "one", title: { default: "Primera", en: "First things" }, elements: [{ type: "text", name: "name", title: { default: "Nombre", en: "Name" } }] },
    { name: "two", title: { default: "Segunda", en: "Second things" }, elements: [{ type: "text", name: "city", title: { default: "Ciudad", en: "City" } }] },
  ],
};

const described = (version: number, definition: object) => ({
  version, definition, languages: ["es", "en"], default_language: "es", language: "", theme: "house.theme", honeypot_field: "website",
});

const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status });

/** What the address answers, in order, to each kind of request; the last answer repeats. */
interface Address {
  get: Array<() => Response>;
  post: Array<() => Response>;
}

const posted: Array<Record<string, unknown>> = [];
const rendered: Model[] = [];
const removed: Model[] = [];
const wear = vi.fn<(themeId: string) => void>();

function serve(address: Address): void {
  const next = (answers: Array<() => Response>) => (answers.length > 1 ? answers.shift()! : answers[0]!)();
  vi.stubGlobal("fetch", vi.fn(async (_url: string, init?: RequestInit) => {
    if (init?.method !== "POST") return next(address.get);
    posted.push(JSON.parse(String(init.body)) as Record<string, unknown>);
    return next(address.post);
  }));
}

/** Open the page at an address; its surveys are kept, and drawn only when a test asks for SurveyJS's own renderer. */
async function opened(address = "/forms/semilla", { drawn = false } = {}): Promise<void> {
  history.replaceState(null, "", address);
  await openPage({
    render(survey, host) {
      rendered.push(survey);
      if (drawn) renderSurvey(survey, host);
      return () => {
        removed.push(survey);
        if (drawn) unmountComponentAtNode(host);
      };
    },
    wear,
  });
}

const survey = () => rendered.at(-1)!;
const notice = () => document.getElementById("notice") as HTMLElement;
const mount = () => document.getElementById("form") as HTMLElement;
const picker = () => document.getElementById("language") as HTMLSelectElement;

/** Press Complete, and wait until SurveyJS has its answer. */
async function complete(asking = survey()): Promise<void> {
  asking.tryComplete();
  await vi.waitFor(() => expect(asking.isNavigationBlocked).toBe(false));
}

beforeEach(() => {
  document.body.className = DOCUMENT[1]!;
  document.body.innerHTML = DOCUMENT[2]!.replace(/<script[\s\S]*?<\/script>/g, "");
  document.documentElement.lang = "en";
  posted.length = rendered.length = removed.length = 0;
  wear.mockClear();
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("the document before its script speaks", () => {
  test("shows nothing while the page starts, and carries a plain message of its own", () => {
    expect(document.body.classList.contains("starting")).toBe(true);
    expect(notice().hidden).toBe(false);
    expect(notice().textContent).toBe("This form could not be loaded. Reload the page to try again.");
  });
});

describe("opening a form", () => {
  test("shows the form in its theme and in the language the address asks for, whatever the case", async () => {
    serve({ get: [() => json(200, described(1, FIRST))], post: [] });
    await opened("/forms/semilla?lang=EN");
    expect(wear).toHaveBeenLastCalledWith("house.theme");
    expect(document.documentElement.lang).toBe("en");
    expect(document.title).toBe("Survey");
    expect(survey().getQuestionByName("name")?.title).toBe("Name");
    expect([...picker().options].map((option) => option.value)).toEqual(["es", "en"]);
    expect(picker().value).toBe("en");
    expect(picker().hidden).toBe(false);
    expect(picker().getAttribute("aria-label")).toBe("Language");
    expect(notice().hidden).toBe(true);
    expect(mount().hidden).toBe(false);
    expect(document.body.classList.contains("starting")).toBe(false);
  });

  test("opens in the form's default language when nothing asks for another", async () => {
    vi.stubGlobal("navigator", { languages: ["gl"] });
    serve({ get: [() => json(200, described(1, FIRST))], post: [] });
    await opened();
    expect(document.documentElement.lang).toBe("es");
    expect(document.title).toBe("Encuesta");
    expect(picker().getAttribute("aria-label")).toBe("Idioma");
  });

  test("says the form takes no responses when the address has none, in the visitor's language", async () => {
    serve({ get: [() => json(404, { error: "not_found" })], post: [] });
    await opened("/forms/semilla?lang=es");
    expect(notice().hidden).toBe(false);
    expect(notice().textContent).toBe("Este formulario no está aceptando respuestas.");
    expect(mount().hidden).toBe(true);
    expect(document.body.classList.contains("starting")).toBe(false);
  });

  test.each([
    ["the server fails", () => json(500, { error: "server_error" })],
    ["the answer does not describe a form", () => json(200, { version: "1" })],
    ["the answer is not JSON", () => new Response("<!doctype html>", { status: 200 })],
  ])("says the form could not be loaded when %s", async (_label, answer) => {
    serve({ get: [answer], post: [] });
    await opened();
    expect(notice().textContent).toBe("The form could not be loaded. Try again in a moment.");
    expect(wear).toHaveBeenLastCalledWith("");
    expect(document.body.classList.contains("starting")).toBe(false);
  });
});

describe("changing the language while filling in", () => {
  test("keeps the answers, names the page in the new language and remembers it in the address", async () => {
    serve({ get: [() => json(200, described(1, FIRST))], post: [] });
    await opened();
    survey().setValue("name", "Ada");
    picker().value = "en";
    picker().dispatchEvent(new Event("change"));
    await vi.waitFor(() => expect(document.documentElement.lang).toBe("en"));
    expect(document.title).toBe("Survey");
    expect(survey().data).toEqual({ name: "Ada" });
    expect(new URL(location.href).searchParams.get("lang")).toBe("en");
  });

  test("the last choice wins when its strings arrive first", async () => {
    serve({ get: [() => json(200, described(1, FIRST))], post: [] });
    await opened("/forms/semilla?lang=en");
    // Spanish loads a file of strings; English is built in and answers at once.
    picker().value = "es";
    picker().dispatchEvent(new Event("change"));
    picker().value = "en";
    picker().dispatchEvent(new Event("change"));
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(document.documentElement.lang).toBe("en");
    expect(picker().value).toBe("en");
    expect(survey().getQuestionByName("name")?.title).toBe("Name");
  });
});

describe("a form in several pages", () => {
  test("shows its progress, and a page the visitor turns to takes the focus at its title", async () => {
    serve({ get: [() => json(200, described(1, PAGED))], post: [] });
    await opened("/forms/semilla?lang=en", { drawn: true });
    await vi.waitFor(() => expect(mount().textContent).toContain("First things"));
    expect(mount().querySelector('[class*="progress"]')).not.toBeNull();
    // Opening the form moves no focus: nothing asks for a keyboard before the visitor does.
    expect(mount().contains(document.activeElement)).toBe(false);
    survey().nextPage();
    await vi.waitFor(() => expect(document.activeElement?.textContent).toBe("Second things"));
    expect(survey().currentPageNo).toBe(1);
    expect(mount().textContent).toContain("City");
    survey().prevPage();
    await vi.waitFor(() => expect(document.activeElement?.textContent).toBe("First things"));
  });

  test("a refusal of an answer on an earlier page returns to that page and shows it on its question", async () => {
    serve({
      get: [() => json(200, described(1, PAGED))],
      post: [() => json(400, { error: "invalid_submission", message: "Check the answers.", fields: { name: ["Too short."] } })],
    });
    await opened("/forms/semilla?lang=en", { drawn: true });
    survey().setValue("name", "A");
    survey().nextPage();
    await vi.waitFor(() => expect(document.activeElement?.textContent).toBe("Second things"));
    const focus = vi.spyOn(HTMLElement.prototype, "focus");
    await complete();
    expect(survey().currentPageNo).toBe(0);
    await vi.waitFor(() => expect(mount().textContent).toContain("Too short."));
    expect(survey().getQuestionByName("name")?.errors.map((error) => error.text)).toEqual(["Too short."]);
    // The question is what the visitor is brought back to: the page's title never takes the focus on the way.
    await vi.waitFor(() => expect(document.activeElement?.tagName).toBe("INPUT"));
    expect(focus.mock.contexts.map((element) => (element as HTMLElement).textContent)).not.toContain("First things");
  });
});

describe("sending the answers", () => {
  test("sends what the form asks, under one id, with the field a person leaves empty", async () => {
    serve({ get: [() => json(200, described(1, FIRST))], post: [() => json(202, { submission_id: "kept" })] });
    await opened("/forms/semilla?lang=en");
    survey().setValue("name", "Ada");
    survey().setValue("colour", "other");
    survey().setComment("colour", "teal");
    survey().setValue("colour", "red");
    await complete();
    expect(posted).toHaveLength(1);
    expect(Object.keys(posted[0]!).sort()).toEqual(["answers", "language", "submission_id", "version", "website"]);
    expect(posted[0]).toMatchObject({ version: 1, language: "en", website: "", answers: { name: "Ada", colour: "red" } });
    expect(posted[0]!.submission_id).toMatch(/^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/);
    expect(survey().state).toBe("completed");
  });

  test("puts a refusal on its question and says the rest", async () => {
    serve({
      get: [() => json(200, described(1, FIRST))],
      post: [() => json(400, {
        error: "invalid_submission", message: "Check the answers.",
        fields: { name: ["Too short."], "colour-Comment": ["Say what the other answer is."], answers: ["Not kept."] },
      })],
    });
    await opened("/forms/semilla?lang=en");
    const said = vi.spyOn(survey(), "notify");
    await complete();
    expect(survey().state).toBe("running");
    expect(survey().getQuestionByName("name")?.errors.map((error) => error.text)).toEqual(["Too short."]);
    expect(survey().getQuestionByName("colour")?.errors.map((error) => error.text)).toEqual(["Say what the other answer is."]);
    expect(said).toHaveBeenCalledWith("Not kept.", "error");
  });

  test("SurveyJS draws the form in the chosen language and a refusal beside its question", async () => {
    serve({
      get: [() => json(200, described(1, FIRST))],
      post: [() => json(400, { error: "invalid_submission", message: "Check the answers.", fields: { name: ["Too short."] } })],
    });
    await opened("/forms/semilla?lang=en", { drawn: true });
    await vi.waitFor(() => expect(mount().textContent).toContain("Name"));
    expect(mount().textContent).toContain("Survey");
    await complete();
    await vi.waitFor(() => expect(mount().textContent).toContain("Too short."));
    picker().value = "es";
    picker().dispatchEvent(new Event("change"));
    await vi.waitFor(() => expect(mount().textContent).toContain("Nombre"));
    expect(mount().textContent).toContain("Encuesta");
  });

  test("says a refusal that is about no question", async () => {
    serve({
      get: [() => json(200, described(1, FIRST))],
      post: [() => json(429, { error: "rate_limited", message: "Too many submissions. Try again in a minute." })],
    });
    await opened("/forms/semilla?lang=en");
    const said = vi.spyOn(survey(), "notify");
    await complete();
    expect(survey().state).toBe("running");
    expect(said).toHaveBeenCalledWith("Too many submissions. Try again in a minute.", "error");
  });

  test("after the form changed, shows the new version with the answers it still asks for, says so, and sends again", async () => {
    serve({
      get: [() => json(200, described(1, FIRST)), () => json(200, described(2, SECOND))],
      post: [() => json(409, { error: "form_changed", message: "Reload." }), () => json(202, { submission_id: "kept" })],
    });
    await opened("/forms/semilla?lang=en");
    const first = survey();
    first.setValue("name", "Ada");
    first.setValue("nickname", "Countess");
    first.setValue("colour", "other");
    first.setComment("colour", "teal");
    const said = vi.spyOn(Model.prototype, "notify");
    first.tryComplete();
    await vi.waitFor(() => expect(rendered).toHaveLength(2));
    const second = survey();
    expect(second).not.toBe(first);
    // The version that was on screen is taken down and finished with.
    expect(removed).toEqual([first]);
    expect(first.isDisposed).toBe(true);
    expect(second.data).toEqual({ name: "Ada" });
    expect(said).toHaveBeenCalledWith(
      "The form changed while you were filling it in. Your answers are kept; please check them and send again.", "info",
    );
    expect(document.documentElement.lang).toBe("en");
    second.setValue("colour", "blue");
    await complete(second);
    expect(posted).toHaveLength(2);
    expect(posted[1]).toMatchObject({ version: 2, answers: { name: "Ada", colour: "blue" } });
    expect(posted[1]!.submission_id).toBe(posted[0]!.submission_id);
    expect(second.state).toBe("completed");
  });

  test("says the form takes no responses when it closed while being filled in", async () => {
    serve({ get: [() => json(200, described(1, FIRST))], post: [() => json(404, { error: "not_found" })] });
    await opened("/forms/semilla?lang=es");
    survey().tryComplete();
    await vi.waitFor(() => expect(notice().hidden).toBe(false));
    expect(notice().textContent).toBe("Este formulario no está aceptando respuestas.");
    expect(mount().hidden).toBe(true);
    expect(picker().hidden).toBe(true);
    expect(removed).toEqual(rendered);
  });

  test("keeps the form usable when the answers could not be sent, in SurveyJS's own words", async () => {
    serve({ get: [() => json(200, described(1, FIRST))], post: [] });
    await opened("/forms/semilla?lang=en");
    const said = vi.spyOn(survey(), "notify");
    // The request fails before it leaves: nothing answers at all.
    vi.stubGlobal("fetch", () => {
      throw new Error("offline");
    });
    await complete();
    expect(survey().state).toBe("running");
    expect(survey().isNavigationBlocked).toBe(false);
    expect(said).toHaveBeenCalledWith("An error occurred and we could not save the results.", "error");
  });
});
