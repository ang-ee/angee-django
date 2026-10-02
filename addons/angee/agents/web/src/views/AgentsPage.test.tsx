// @vitest-environment happy-dom

import * as React from "react";
import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";
import { Form, parsePageActions, type FormProps, type ResourceListProps } from "@angee/ui";

import { AGENT_LIFECYCLE_FIELDS } from "./agent-actions";
import { AgentsPage, TemplatesPage } from "./AgentsPage";

// Keep the real declarations and parsers; only the page mount and transports are replaced.
const mocks = vi.hoisted(() => ({ props: null as ResourceListProps | null }));

vi.mock("@angee/ui", async () => {
  const actual = await vi.importActual<typeof import("@angee/ui")>("@angee/ui");
  return {
    ...actual,
    ResourceList: (props: ResourceListProps) => {
      mocks.props = props;
      return null;
    },
    useRecordActionMutation: () => [vi.fn(async () => undefined), { fetching: false, error: null }],
  };
});

vi.mock("@angee/iam", () => ({
  usePrincipalAccessRecordTab: () => ({ id: "access", label: "Access", render: () => null }),
}));
vi.mock("./AgentChat", () => ({ AgentChat: () => null }));
vi.mock("./AgentProvisioning", () => ({ AgentProvisioning: () => null }));

afterEach(() => {
  cleanup();
  mocks.props = null;
});

function formActionIds(): string[] {
  const form = React.Children.toArray(mocks.props?.children).find(
    (child) => React.isValidElement(child) && child.type === Form,
  );
  if (!React.isValidElement<FormProps>(form)) throw new Error("Expected a Form declaration");
  return parsePageActions(form.props.children).map(({ id }) => id);
}

describe("agent record page", () => {
  test("declares every lifecycle verb and selects the facts they read", () => {
    render(<AgentsPage />);

    expect(formActionIds()).toEqual(["provision", "adopt", "replace", "reprovision", "deprovision"]);
    expect(mocks.props?.returning).toEqual(expect.arrayContaining([...AGENT_LIFECYCLE_FIELDS]));
  });

  test("templates carry no operator lifecycle actions", () => {
    render(<TemplatesPage />);

    expect(formActionIds()).toEqual([]);
  });
});
