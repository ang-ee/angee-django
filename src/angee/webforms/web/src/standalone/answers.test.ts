import { Model } from "survey-core";
import { describe, expect, test } from "vitest";

import { answersToSend, restoreAnswers } from "./answers";

/** The version a visitor started filling in. */
const FIRST = {
  pages: [{
    elements: [
      { type: "text", name: "name" },
      { type: "text", name: "nickname" },
      { type: "radiogroup", name: "colour", choices: ["red", "blue"], showOtherItem: true },
      { type: "checkbox", name: "likes", choices: ["tea", "coffee"], showOtherItem: true },
      { type: "dropdown", name: "size", choices: ["s", "m"], showOtherItem: true },
    ],
  }],
};

/** What the visitor had answered under it, as SurveyJS holds it. */
function given(answer: (survey: Model) => void): object {
  const survey = new Model(FIRST);
  answer(survey);
  return survey.data as object;
}

/** The survey of another version with those answers put back. */
function reopened(definition: object, answers: object): Model {
  const survey = new Model(definition);
  restoreAnswers(survey, answers);
  return survey;
}

describe("restoreAnswers", () => {
  test("keeps every answer when the version asks the same questions", () => {
    const answers = given((survey) => {
      survey.setValue("name", "Ada");
      survey.setValue("colour", "other");
      survey.setComment("colour", "teal");
      survey.setValue("likes", ["tea", "other"]);
      survey.setComment("likes", "mate");
    });
    expect(reopened(FIRST, answers).data).toEqual({
      name: "Ada", colour: "other", "colour-Comment": "teal", likes: ["tea", "other"], "likes-Comment": "mate",
    });
  });

  test("drops the answer to a question the new version removed, and keeps the others", () => {
    const answers = given((survey) => {
      survey.setValue("name", "Ada");
      survey.setValue("nickname", "Countess");
    });
    const without = { pages: [{ elements: FIRST.pages[0]!.elements.filter((element) => element.name !== "nickname") }] };
    expect(reopened(without, answers).data).toEqual({ name: "Ada" });
  });

  test("drops the \"Other\" text of a question the new version removed", () => {
    const answers = given((survey) => {
      survey.setValue("name", "Ada");
      survey.setValue("size", "other");
      survey.setComment("size", "extra large");
    });
    const without = { pages: [{ elements: FIRST.pages[0]!.elements.filter((element) => element.name !== "size") }] };
    expect(reopened(without, answers).data).toEqual({ name: "Ada" });
  });

  test("drops \"Other\" and its text from a question that no longer offers the choice, and keeps its listed choices", () => {
    const answers = given((survey) => {
      survey.setValue("colour", "other");
      survey.setComment("colour", "teal");
      survey.setValue("likes", ["tea", "other"]);
      survey.setComment("likes", "mate");
      survey.setValue("size", "m");
    });
    const listedOnly = {
      pages: [{ elements: FIRST.pages[0]!.elements.map(({ showOtherItem: _offered, ...element }) => element) }],
    };
    expect(reopened(listedOnly, answers).data).toEqual({ likes: ["tea"], size: "m" });
  });

  test("drops a choice the new version no longer lists", () => {
    const answers = given((survey) => {
      survey.setValue("size", "m");
      survey.setValue("likes", ["tea", "coffee"]);
    });
    const fewer = {
      pages: [{
        elements: [
          { type: "checkbox", name: "likes", choices: ["tea"] },
          { type: "dropdown", name: "size", choices: ["s"] },
        ],
      }],
    };
    expect(reopened(fewer, answers).data).toEqual({ likes: ["tea"] });
  });
});

describe("answersToSend", () => {
  test("leaves out the text of an \"Other\" choice the visitor moved away from", () => {
    const survey = new Model(FIRST);
    survey.setValue("colour", "other");
    survey.setComment("colour", "teal");
    survey.setValue("colour", "red");
    survey.setValue("likes", ["tea", "other"]);
    survey.setComment("likes", "mate");
    survey.setValue("likes", ["tea"]);
    // SurveyJS still holds both texts at this point.
    expect(survey.data).toMatchObject({ "colour-Comment": "teal", "likes-Comment": "mate" });
    expect(answersToSend(survey)).toEqual({ colour: "red", likes: ["tea"] });
  });

  test("sends the text of an \"Other\" choice that is taken", () => {
    const survey = new Model(FIRST);
    survey.setValue("name", "Ada");
    survey.setValue("size", "other");
    survey.setComment("size", "extra large");
    expect(answersToSend(survey)).toEqual({ name: "Ada", size: "other", "size-Comment": "extra large" });
  });
});
