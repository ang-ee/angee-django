/**
 * The UX component ontology guard: addon web sources compose `@angee/ui` owners
 * and never re-implement a primitive, fragment or region of their own. The rules
 * mirror the frontend guidelines ("Component ontology" and the owner rules); the
 * architecture guardrails test runs them over every addon root and keeps a seeded
 * violation per rule. Heuristics are deliberately plain so a reader can predict a
 * hit from the source alone; an allowlist entry names the file, the rule and why.
 */
import { readFileSync } from "node:fs";
import ts from "typescript";

export type UxGuardRule =
  | "raw-element"
  | "lucide-import"
  | "class-literal"
  | "locale-bypass"
  | "deleted-owner-import"
  | "local-recipe"
  | "local-tone-map";

export interface UxGuardViolation {
  rule: UxGuardRule;
  file: string;
  line: number;
  detail: string;
  owner: string;
}

export interface UxGuardAllowance {
  /** Repo-relative path (the guardrails test resolves it against the monorepo root). */
  file: string;
  rule: UxGuardRule;
  reason: string;
}

/** Files the guard tolerates, each with the owner-level reason; keep this list short. */
export const UX_GUARD_ALLOWANCES: readonly UxGuardAllowance[] = [
  {
    file: "addons/angee/money/web/src/widgets/money.tsx",
    rule: "locale-bypass",
    reason: "formatMoney is the money owner: it composes Intl.NumberFormat once for every consumer.",
  },
];

/** Raw elements the owners replace: `<tag>` → owner to compose. */
const RAW_ELEMENT_OWNERS: Readonly<Record<string, string>> = {
  button: "Button (or ActionTrigger / PaneToolbarAction)",
  input: "Input, SearchInput, Combobox, Checkbox or Switch",
  select: "Select or Combobox",
  textarea: "Textarea",
  table: "Table (or ListView / EditableLines)",
  dialog: "Dialog, DialogForm, ActionFormDialog or MutationDialog",
  form: "FormRoot, FormView or DialogForm",
};

/** Class names that resolve to nothing since the token cleanup, with their replacement. */
const DELETED_CLASS_NAMES: Readonly<Record<string, string>> = {
  "text-xs": "text-12",
  "text-11": "text-2xs",
  "text-14": "text-sm",
  "bg-danger-bg": "ErrorBanner / toneClass(\"danger\", \"soft\")",
  "border-danger-border": "ErrorBanner / toneClass(\"danger\", \"soft\")",
  "text-fg-3": "text-fg-muted",
  "bg-surface-raised": "bg-sheet",
};

/** Owners whose presence in a file marks its size classes as control sizes. */
const CONTROL_OWNERS = ["Button", "Chip", "ToolbarButton", "PaneToolbar", "PaneToolbarAction", "Toolbar"] as const;
/** Owners whose presence in a file marks a local `tv()` recipe as a duplicate. */
const RECIPE_OWNERS = ["Button", "Chip", "PaneRow", "RecordHeader", "CommandBand", "PaneToolbar"] as const;

/** Exports that lane A removed from `@angee/ui`, with their replacement. */
const DELETED_UI_EXPORTS: Readonly<Record<string, string>> = {
  Badge: "Chip",
  Tag: "Chip",
  Label: "FieldLabel (inside FieldRoot)",
  FieldRow: "FieldRoot + FieldLabel",
  TextInput: "Input",
  Divider: "Separator",
  DebouncedSearchInput: "SearchInput",
  PopoverInput: "SearchInput inside PopoverContent",
  ToolbarInput: "SearchInput",
  TimelineView: "PaneRows + PaneRow + PaneLabel",
  TimelineEntry: "PaneRow",
  formatDuration: "formatDateTime / formatTime",
  PaneToolbarIconButton: "Button size=\"iconSm\" (PaneToolbarAction)",
  ChatHeaderAction: "Button size=\"sm\" variant=\"ghost\"",
};

const TONE_NAMES = new Set(["success", "warning", "danger", "neutral", "info", "brand", "muted"]);
const LOCALE_METHODS = new Set(["toLocaleString", "toLocaleDateString", "toLocaleTimeString"]);
const INTL_FORMATTERS = new Set(["NumberFormat", "DateTimeFormat"]);
const DATE_STRINGIFIERS = new Set(["toString", "toISOString", "toDateString", "toTimeString", "toUTCString"]);
const CONTROL_SIZE_CLASS = /(?:^|\s)(?:h|size)-(?:5|6|7|8|9|10|11)(?=\s|$)/;
const PIXEL_SIZE_CLASS = /(?:^|\s)(?:text|h|w|size|min-h|max-h|min-w|max-w|leading)-\[\d+(?:\.\d+)?px\]/;

/** Scan one addon source text; `file` is only echoed into the violations. */
export function uxGuardViolations(file: string, text: string): UxGuardViolation[] {
  const source = ts.createSourceFile(
    file,
    text,
    ts.ScriptTarget.Latest,
    true,
    file.endsWith(".tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS,
  );
  const violations: UxGuardViolation[] = [];
  const report = (rule: UxGuardRule, node: ts.Node, detail: string, owner: string): void => {
    const { line } = source.getLineAndCharacterOfPosition(node.getStart(source));
    violations.push({ rule, file, line: line + 1, detail, owner });
  };

  const uiImports = new Set<string>();
  const lucideBindings = new Map<string, ts.Node>();
  let importsDateFns = false;
  for (const statement of source.statements) {
    if (!ts.isImportDeclaration(statement) || !ts.isStringLiteral(statement.moduleSpecifier)) continue;
    const specifier = statement.moduleSpecifier.text;
    if (specifier === "lucide-react" || specifier.startsWith("lucide-react/")) {
      const bindings = statement.importClause?.namedBindings;
      if (bindings && ts.isNamedImports(bindings)) {
        for (const binding of bindings.elements) lucideBindings.set(binding.name.text, binding);
      } else if (statement.importClause) {
        lucideBindings.set(statement.importClause.name?.text ?? "*", statement);
      }
    }
    if (specifier === "date-fns" || specifier.startsWith("date-fns/")) importsDateFns = true;
    if (specifier !== "@angee/ui" && !specifier.startsWith("@angee/ui/")) continue;
    const bindings = statement.importClause?.namedBindings;
    if (!bindings || !ts.isNamedImports(bindings)) continue;
    for (const binding of bindings.elements) {
      const imported = (binding.propertyName ?? binding.name).text;
      uiImports.add(imported);
      const replacement = DELETED_UI_EXPORTS[imported];
      if (replacement) {
        report("deleted-owner-import", binding, `imports deleted @angee/ui export ${imported}`, replacement);
      }
    }
  }
  // An addon registers its own glyphs from lucide in its manifest `icons: {}` block;
  // rendering a lucide component anywhere else bypasses the registry.
  for (const [name, binding] of lucideBindings) {
    if (!usedOutsideIconRegistry(source, name)) continue;
    report("lucide-import", binding, `renders the lucide icon ${name} directly`, "Glyph with a registered name (register it in the manifest icons block)");
  }
  const rendersControls = CONTROL_OWNERS.some((owner) => uiImports.has(owner));
  const composesRecipeOwner = RECIPE_OWNERS.some((owner) => uiImports.has(owner));
  const statusToneBindings = statusToneContributionBindings(source);

  const visit = (node: ts.Node): void => {
    if (ts.isJsxOpeningLikeElement(node) && ts.isIdentifier(node.tagName)) {
      const tag = node.tagName.text;
      const owner = RAW_ELEMENT_OWNERS[tag];
      if (owner && !(tag === "input" && jsxAttributeText(node, "type") === "hidden")) {
        report("raw-element", node, `renders a raw <${tag}>`, owner);
      }
    } else if (ts.isStringLiteralLike(node) || ts.isTemplateHead(node) || ts.isTemplateMiddle(node) || ts.isTemplateTail(node)) {
      checkClassLiteral(node, node.text, rendersControls, report);
    } else if (ts.isPropertyAccessExpression(node)) {
      if (LOCALE_METHODS.has(node.name.text) && ts.isCallExpression(node.parent) && node.parent.expression === node) {
        report("locale-bypass", node, `calls ${node.name.text}()`, "formatDate / formatDateTime / formatTime / formatNumber");
      } else if (
        ts.isIdentifier(node.expression) && node.expression.text === "Intl" && INTL_FORMATTERS.has(node.name.text)
      ) {
        report("locale-bypass", node, `constructs Intl.${node.name.text}`, "formatNumber / formatDate");
      } else if (
        DATE_STRINGIFIERS.has(node.name.text)
        && ts.isCallExpression(node.parent) && node.parent.expression === node
        && isNewDate(node.expression) && insideJsxExpression(node)
      ) {
        report("locale-bypass", node, `renders new Date(...).${node.name.text}()`, "formatDate / formatDateTime");
      }
    } else if (ts.isCallExpression(node) && ts.isIdentifier(node.expression)) {
      const callee = node.expression.text;
      if (importsDateFns && callee === "format" && node.arguments[1] && ts.isStringLiteralLike(node.arguments[1])) {
        report("locale-bypass", node, `formats with the date-fns pattern "${node.arguments[1].text}"`, "formatDate / formatDateTime / formatDayLabel");
      } else if (callee === "tv" && composesRecipeOwner && node.arguments[0] && ts.isObjectLiteralExpression(node.arguments[0])) {
        report(
          "local-recipe",
          node,
          "declares a local tv() recipe beside a composed owner",
          `the owner's variants (${RECIPE_OWNERS.filter((owner) => uiImports.has(owner)).join(", ")})`,
        );
      }
    } else if (ts.isObjectLiteralExpression(node) && isLocalToneMap(node) && !statusToneBindings.has(node)) {
      report("local-tone-map", node, "maps status values to tones locally", "statusTones in the addon manifest + statusTone / StatusBadge");
    }
    ts.forEachChild(node, visit);
  };
  visit(source);
  return violations;
}

/** True when `name` is referenced anywhere but its import and a manifest `icons: {}` literal. */
function usedOutsideIconRegistry(source: ts.SourceFile, name: string): boolean {
  let used = false;
  const visit = (node: ts.Node, inRegistry: boolean): void => {
    if (used || ts.isImportDeclaration(node)) return;
    if (ts.isPropertyAssignment(node) && propertyKeyText(node.name) === "icons" && ts.isObjectLiteralExpression(node.initializer)) {
      ts.forEachChild(node.initializer, (child) => visit(child, true));
      return;
    }
    if (!inRegistry && ts.isIdentifier(node) && node.text === name
      && !(ts.isPropertyAccessExpression(node.parent) && node.parent.name === node)) {
      used = true;
      return;
    }
    ts.forEachChild(node, (child) => visit(child, inRegistry));
  };
  visit(source, false);
  return used;
}

function checkClassLiteral(
  node: ts.Node,
  text: string,
  rendersControls: boolean,
  report: (rule: UxGuardRule, node: ts.Node, detail: string, owner: string) => void,
): void {
  if (!/[a-z]-[a-z0-9[]/.test(text)) return;
  if (text.includes("bg-brand-soft") && text.includes("text-brand-soft-text")) {
    report("class-literal", node, "spells the selected surface by hand (bg-brand-soft + text-brand-soft-text)", "the `selected` utility");
  }
  for (const token of text.split(/\s+/)) {
    const name = token.replace(/^[a-z-]+:/, "");
    const replacement = DELETED_CLASS_NAMES[name];
    if (replacement) report("class-literal", node, `uses the deleted class ${name}`, replacement);
  }
  if (PIXEL_SIZE_CLASS.test(text)) {
    report("class-literal", node, "sizes with a raw pixel class", "a size token (text roles, --control-h-*, spacing scale)");
  }
  if (rendersControls && CONTROL_SIZE_CLASS.test(text)) {
    report("class-literal", node, "sizes a control with a raw h-N/size-N class", "the owner's size axis (Button size, h-control-xs, h-btn-*)");
  }
}

function jsxAttributeText(node: ts.JsxOpeningLikeElement, name: string): string | null {
  const attribute = node.attributes.properties.find((entry) =>
    ts.isJsxAttribute(entry) && ts.isIdentifier(entry.name) && entry.name.text === name);
  if (!attribute || !ts.isJsxAttribute(attribute) || !attribute.initializer) return null;
  if (ts.isStringLiteral(attribute.initializer)) return attribute.initializer.text;
  if (ts.isJsxExpression(attribute.initializer) && attribute.initializer.expression
    && ts.isStringLiteralLike(attribute.initializer.expression)) {
    return attribute.initializer.expression.text;
  }
  return null;
}

function isNewDate(expression: ts.Expression): boolean {
  const inner = ts.isParenthesizedExpression(expression) ? expression.expression : expression;
  return ts.isNewExpression(inner) && ts.isIdentifier(inner.expression) && inner.expression.text === "Date";
}

function insideJsxExpression(node: ts.Node): boolean {
  for (let current: ts.Node | undefined = node.parent; current; current = current.parent) {
    if (ts.isJsxExpression(current)) return true;
    if (ts.isFunctionLike(current) && !ts.isArrowFunction(current)) return false;
  }
  return false;
}

/** `{ DONE: "success", FAILED: "danger" }`: two or more upper-case enum keys, every value a tone name. */
function isLocalToneMap(node: ts.ObjectLiteralExpression): boolean {
  if (node.properties.length < 2) return false;
  return node.properties.every((property) =>
    ts.isPropertyAssignment(property)
    && /^[A-Z][A-Z0-9_]*$/.test(propertyKeyText(property.name))
    && ts.isStringLiteralLike(property.initializer)
    && TONE_NAMES.has(property.initializer.text));
}

function propertyKeyText(name: ts.PropertyName): string {
  return ts.isIdentifier(name) || ts.isStringLiteralLike(name) || ts.isNumericLiteral(name) ? name.text : "";
}

/**
 * Object literals that feed a `statusTones` manifest contribution: directly as
 * the property value, or through a module-level `const` that a `statusTones:`
 * property in the same file references.
 */
function statusToneContributionBindings(source: ts.SourceFile): Set<ts.Node> {
  const bindings = new Set<ts.Node>();
  const referenced = new Set<string>();
  const visit = (node: ts.Node): void => {
    if (ts.isPropertyAssignment(node) && propertyKeyText(node.name) === "statusTones") {
      if (ts.isObjectLiteralExpression(node.initializer)) bindings.add(node.initializer);
      else if (ts.isIdentifier(node.initializer)) referenced.add(node.initializer.text);
    }
    ts.forEachChild(node, visit);
  };
  visit(source);
  for (const statement of source.statements) {
    if (!ts.isVariableStatement(statement)) continue;
    for (const declaration of statement.declarationList.declarations) {
      if (!ts.isIdentifier(declaration.name) || !declaration.initializer) continue;
      const exported = statement.modifiers?.some((modifier) => modifier.kind === ts.SyntaxKind.ExportKeyword) ?? false;
      // An exported map reaches a manifest in another file (the addon index); a local
      // one must be wired here.
      if (!exported && !referenced.has(declaration.name.text)) continue;
      const initializer = ts.isAsExpression(declaration.initializer) || ts.isSatisfiesExpression(declaration.initializer)
        ? declaration.initializer.expression
        : declaration.initializer;
      if (ts.isObjectLiteralExpression(initializer) && (/_TONES$|Tones$/.test(declaration.name.text) || referenced.has(declaration.name.text))) {
        bindings.add(initializer);
      }
    }
  }
  return bindings;
}

/** Scan a file on disk. */
export function uxGuardViolationsInFile(file: string): UxGuardViolation[] {
  return uxGuardViolations(file, readFileSync(file, "utf8"));
}

/** One line per violation: `path:line rule: detail — compose <owner>`. */
export function formatUxGuardViolation(violation: UxGuardViolation, relativeFile: string): string {
  return `${relativeFile}:${violation.line} ${violation.rule}: ${violation.detail} — compose ${violation.owner}`;
}
