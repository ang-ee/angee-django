// @vitest-environment happy-dom

import { cleanup, render, screen } from "@testing-library/react";
import * as React from "react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  formProps: null as Record<string, unknown> | null,
}));

const CATEGORY_OPTIONS = ["triage", "backlog", "unstarted", "started", "completed", "canceled", "duplicate"]
  .map((value) => ({ value, label: value }));

vi.mock("@angee/ui", () => ({
  Badge: ({ children }: { children?: React.ReactNode }) => <span data-badge>{children}</span>,
  Field: () => null,
  Form: (props: Record<string, unknown>) => {
    mocks.formProps = props;
    return null;
  },
  Group: () => null,
  useEnumOptions: (_resource: string, field: string) => (field === "category" ? CATEGORY_OPTIONS : []),
}));

vi.mock("./i18n", () => ({
  useWorkT: () => (key: string) => key,
}));

import { QUEUE_FORM_SECTIONS, QUEUE_STAGES_SECTION, QueueSettingsForm, useQueueFormDeclaration } from "./queue-form";

interface LineField {
  name: string;
  label?: string;
  description?: string;
  options?: readonly { value: string; disabled?: boolean }[];
}

interface SupplementalColumn {
  key: string;
  align?: string;
  render: (row: Record<string, unknown>, parent: null, index: number, context: { formIsDirty: boolean }) => React.ReactNode;
}

type SectionChild = { sequence?: number; content: React.ReactElement<Record<string, unknown>> };

function Declaration(props: Parameters<typeof useQueueFormDeclaration>[0]): React.ReactElement {
  return useQueueFormDeclaration(props);
}

beforeEach(() => {
  mocks.formProps = null;
});

afterEach(cleanup);

describe("queue settings sections", () => {
  const sections = QUEUE_FORM_SECTIONS as unknown as Readonly<Record<string, SectionChild>>;

  test("are work.Queue#sections children in the form's order, the Stages lines last", () => {
    expect(Object.entries(sections).map(([id, child]) => [id, child.sequence])).toEqual([
      ["work.queue-identity", 10],
      ["work.queue-triage", 20],
      ["work.queue-cadence", 30],
      ["work.queue-estimates", 40],
      ["work.queue-stages", 50],
    ]);
    expect(QUEUE_STAGES_SECTION).toBe("work.queue-stages");
  });

  test("the Stages child declares the form's lines, so narrowing to it keeps only them", () => {
    expect(sections[QUEUE_STAGES_SECTION]!.content.props.lines).toBe(true);
    for (const [id, child] of Object.entries(sections)) {
      if (id !== QUEUE_STAGES_SECTION) expect(child.content.props.lines).toBeUndefined();
    }
  });

  test("the description is the identity section's body, so it leaves with that section", () => {
    const identity = React.Children.toArray(sections["work.queue-identity"]!.content.props.children as React.ReactNode)
      .map((child) => (child as React.ReactElement<{ name: string; body?: boolean }>).props);
    expect(identity[0]).toMatchObject({ name: "description", body: true });
  });
});

describe("queue settings form declaration", () => {
  test("titles the queue form, shows name, category and tone first, and keeps the rule flags behind the menu with their help", () => {
    render(<Declaration />);

    expect(mocks.formProps?.resource).toBe("work.Queue");
    expect(mocks.formProps?.layout).toBeUndefined();
    expect(mocks.formProps?.linePrimaryFields).toEqual(["name", "category", "tone"]);
    const fields = mocks.formProps?.lineFields as readonly LineField[];
    expect(fields.map((field) => field.name)).toEqual(["name", "category", "tone", "rule_owned", "conceals"]);
    const byName = new Map(fields.map((field) => [field.name, field]));
    expect(byName.get("rule_owned")).toMatchObject({ label: "stage.ruleOwned", description: "stage.ruleOwned.help" });
    expect(byName.get("conceals")).toMatchObject({ label: "stage.conceals", description: "stage.conceals.help" });
    const title = mocks.formProps?.children as React.ReactElement<{ name: string; title?: boolean }>;
    expect(title.props).toMatchObject({ name: "name", title: true });
  });

  test("a stage picks only a custom category; system categories stay listed but disabled", () => {
    render(<Declaration />);

    const category = (mocks.formProps?.lineFields as readonly LineField[]).find((field) => field.name === "category");
    expect(category?.options?.filter((option) => option.disabled).map((option) => option.value))
      .toEqual(["triage", "duplicate"]);
    expect(category?.options?.filter((option) => !option.disabled).map((option) => option.value))
      .toEqual(["backlog", "unstarted", "started", "completed", "canceled"]);
  });

  test("each stage shows its rule behaviour as left-aligned badges from the live row", () => {
    render(<Declaration />);

    const [badges] = mocks.formProps?.lineSupplementalColumns as readonly SupplementalColumn[];
    expect(badges!.align).toBe("left");
    cleanup();
    render(<>
      {badges!.render({ rule_owned: true, conceals: false }, null, 0, { formIsDirty: false })}
      {badges!.render({ rule_owned: false, conceals: true }, null, 1, { formIsDirty: false })}
      {badges!.render({ rule_owned: false, conceals: false }, null, 2, { formIsDirty: false })}
    </>);
    expect(screen.getAllByText("stage.ruleOwned")).toHaveLength(1);
    expect(screen.getAllByText("stage.conceals")).toHaveLength(1);
  });

  test("a product's own route renders one queue's settings with its FormView props", () => {
    const contextLine = () => "Which pains this queue takes";
    render(<QueueSettingsForm id="que_pains" contextLine={contextLine} />);

    expect(mocks.formProps).toMatchObject({ resource: "work.Queue", id: "que_pains", contextLine });
    expect(mocks.formProps?.lineFields).toBeDefined();
  });
});
