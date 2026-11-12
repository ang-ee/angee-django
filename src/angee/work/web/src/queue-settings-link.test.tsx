// @vitest-environment happy-dom

import { render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { expect, test } from "vitest";
import { ModelMetadataProvider, schemaFieldMetadataFromDataResources } from "@angee/metadata";
import { testDataResource } from "@angee/metadata/testing";
import { AppRuntimeProvider, createRouteHref, type AppRuntime } from "@angee/ui";

import { QueueSettingsLink } from "./queue-settings-link";

const metadata = schemaFieldMetadataFromDataResources([testDataResource("work.Queue")]);
const routed = {
  routeHref: createRouteHref([{ name: "work.queues", path: "/work/queues" }, { name: "work.queues.record", path: "/work/queues/$id" }]),
  routesByResource: { "work.Queue": { collection: "work.queues", record: { name: "work.queues.record", param: "id" } } },
};

function Composed({ runtime, children }: { runtime: Partial<AppRuntime>; children: ReactNode }) {
  return <ModelMetadataProvider metadata={metadata}><AppRuntimeProvider runtime={runtime}>{children}</AppRuntimeProvider></ModelMetadataProvider>;
}

test("opens the queue's record wherever composition routes it", () => {
  render(<Composed runtime={routed}><QueueSettingsLink queueId="que_eng" /></Composed>);
  // The shared Button renders the NavLink as its element, so the anchor carries the button role.
  expect(screen.getByRole("button", { name: "Queue settings" }).getAttribute("href")).toBe("/work/queues/que_eng");
});

test("renders nothing when the queue record is not routed or no queue is open", () => {
  expect(render(<Composed runtime={{}}><QueueSettingsLink queueId="que_eng" /></Composed>).container.firstChild).toBeNull();
  expect(render(<Composed runtime={routed}><QueueSettingsLink queueId="" /></Composed>).container.firstChild).toBeNull();
});
