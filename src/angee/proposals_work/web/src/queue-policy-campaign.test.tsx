import { Field, type ContainerChild, type ContainerEntry, type FieldDescriptor } from "@angee/ui";
import { Children, isValidElement, type ReactNode } from "react";
import { expect, test } from "vitest";

import proposalsWork from "./index";

test.each([
  { permissions: undefined, readOnly: true },
  { permissions: null, readOnly: true },
  { permissions: "write", readOnly: true },
  { permissions: [], readOnly: true },
  { permissions: ["manage"], readOnly: true },
  { permissions: ["respond", "ask"], readOnly: true },
  { permissions: ["write"], readOnly: false },
  { permissions: ["manage", "write"], readOnly: false },
])("queue picker readOnly=$readOnly for $permissions", ({ permissions, readOnly }) => {
  const entry = proposalsWork.containers?.["proposals.Round#sections"] as ContainerEntry<ReactNode> | undefined;
  const section = entry?.["proposals-work.questions"] as ContainerChild<ReactNode> | undefined;
  expect(section).toBeDefined();
  const content = section?.content;
  if (!isValidElement<{ children?: ReactNode }>(content)) throw new Error("Expected the native form group");
  const fields = Children.toArray(content.props.children).filter(
    (child) => isValidElement<FieldDescriptor>(child) && child.type === Field,
  );
  const queue = fields.find((child) => isValidElement<FieldDescriptor>(child) && child.props.name === "clarification_queue");
  if (!isValidElement<FieldDescriptor>(queue) || !queue.props.resolve) throw new Error("Expected the queue field");
  expect(queue.props.resolve({ permissions })).toEqual({ name: "clarification_queue", readOnly });
});
