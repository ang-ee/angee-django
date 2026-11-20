import { QuestionSelectBase, type Model } from "survey-core";

/**
 * Put the answers given so far into a survey, keeping only what its version asks.
 *
 * The server admits one key for each question of the version it serves and
 * nothing else. Answers given under another version may hold a key the new one
 * has no question for; sent again, it would be refused every time, with no
 * question on the page to correct.
 */
export function restoreAnswers(survey: Model, answers: object): void {
  survey.data = answers;
  // SurveyJS drops the answers no question takes, with the text of their "Other" choice,
  // and the choices a question no longer lists.
  survey.clearIncorrectValues(true);
  // It keeps "Other" and its text on a question that no longer offers the choice, where
  // nothing on the page shows either.
  for (const question of survey.getAllQuestions()) {
    if (!(question instanceof QuestionSelectBase) || question.showOtherItem) continue;
    const other: unknown = question.otherItem.value;
    const value: unknown = question.value;
    if (Array.isArray(value)) {
      if (value.includes(other)) question.value = value.filter((choice) => choice !== other);
    } else if (value === other) {
      question.clearValue();
    }
    survey.setComment(question.getValueName(), "");
  }
}

/**
 * The answers as SurveyJS keeps them once a survey is complete: without the text
 * of an "Other" choice that is not taken. SurveyJS drops that text only after the
 * moment the page sends, and the server refuses the text without its choice.
 */
export function answersToSend(survey: Model): object {
  for (const question of survey.getAllQuestions()) question.clearUnusedValues();
  return survey.data as object;
}
