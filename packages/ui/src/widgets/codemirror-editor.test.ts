// @vitest-environment happy-dom

import { EditorView } from "@codemirror/view";
import { basicSetup } from "codemirror";
import { afterEach, describe, expect, test } from "vitest";

import { CODEMIRROR_THEME } from "./codemirror-editor";

/** The injected CSS rules whose selector names `.cm-cursor`. */
function cursorRules(): string[] {
  const sheets = [...(document.adoptedStyleSheets ?? []), ...Array.from(document.styleSheets)];
  return sheets
    .flatMap((sheet) => Array.from(sheet.cssRules, (rule) => rule.cssText.replace(/\s+/g, " ")))
    .filter((rule) => rule.split("{")[0]!.includes(".cm-cursor"));
}

describe("CODEMIRROR_THEME", () => {
  let view: EditorView | undefined;
  afterEach(() => {
    view?.destroy();
    view = undefined;
  });

  test("colours the drawn cursor, which replaces the native caret", () => {
    // basicSetup's drawSelection hides the native caret and draws `.cm-cursor`,
    // black unless a theme says otherwise, so `caretColor` alone leaves the
    // cursor invisible on a dark sheet.
    view = new EditorView({ extensions: [basicSetup, CODEMIRROR_THEME], parent: document.body });
    expect(cursorRules().some((rule) => /border-left-color: ?var\(--brand\)/.test(rule))).toBe(true);
  });
});
